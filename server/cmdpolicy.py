"""Classify shell commands as read-only or mutating.

Used to let the agent run harmless inspection commands (docker ps, ls, df …)
without a per-action approval tap, while anything that writes, deletes or
changes state still asks first. The classifier is deliberately conservative:
everything it does not positively recognise as read-only counts as mutating —
a false "mutate" only costs the user a tap, a false "read" would skip one.

"Read-only" also means "stays on this server". A command that talks to an
arbitrary internet host is a way to *move data off the box*: a model that was
steered by text it read (a log line, a web page, a file) can put a secret into
a URL, a DNS name or a ping payload just as well as into a POST body. So
network commands only count as read-only when every destination is local —
loopback, a private address, or a single-label name such as a Docker service.
`curl http://nextcloud/status.php` runs without a tap; `curl https://x.example`
asks first.
"""

from __future__ import annotations

import ipaddress
import re
import shlex
from urllib.parse import urlsplit

# Commands that never change server state, regardless of arguments
# (writing anywhere would need a shell redirect, which is checked separately).
_ALWAYS_READ = {
    "ls", "dir", "cat", "head", "tail", "wc", "cut",
    "tr", "column", "diff", "cmp", "strings", "stat", "readlink",
    "basename", "dirname", "realpath", "pwd", "echo", "printf", "true", "false",
    "test", "[", "which", "whereis", "type",
    "grep", "egrep", "fgrep", "zgrep", "ag",
    "du", "df", "free", "uptime", "cal", "uname", "arch",
    "whoami", "id", "groups", "last", "w", "who", "users",
    "ps", "pgrep", "pstree", "lsof", "vmstat", "iostat", "mpstat", "nproc",
    "lscpu", "lsmem", "lsblk", "lsusb", "lspci", "blkid",
    "ss", "netstat",
    "md5sum", "sha1sum", "sha256sum", "sha512sum", "cksum", "b2sum",
    "printenv", "locale", "jq", "od",
    "nl", "tac", "zcat", "seq", "expr", "sleep",
}

# Read-only *unless* a specific flag or argument shape turns them into a write
# or a program launcher; each has its own check below.
_CONDITIONAL_READ = {
    "sort", "uniq", "date", "hostname", "dmesg", "xxd", "tree", "less", "more",
    "file", "rg", "lastlog", "getent",
}

# Commands that open network connections. Read-only only when every
# destination is local (see the module docstring).
_NETWORK = {"curl", "wget", "http", "https", "dig", "nslookup", "host",
            "ping", "ping6", "traceroute", "traceroute6", "tracepath", "tracepath6"}

_DOCKER_READ_SUB = {
    "ps", "images", "inspect", "logs", "top", "port", "version", "info",
    "history", "diff", "stats",
}
_DOCKER_READ_PAIRS = {
    ("system", "df"), ("system", "info"), ("volume", "ls"), ("volume", "inspect"),
    ("network", "ls"), ("network", "inspect"), ("image", "ls"), ("image", "inspect"),
    ("image", "history"), ("container", "ls"), ("container", "inspect"),
    ("container", "logs"), ("container", "top"), ("container", "port"),
    ("container", "stats"), ("container", "diff"), ("compose", "ps"),
    ("compose", "config"), ("compose", "logs"), ("compose", "top"),
    ("compose", "version"), ("context", "ls"), ("plugin", "ls"),
}
_GIT_READ = {"status", "log", "diff", "show", "blame", "shortlog", "describe",
             "reflog", "ls-files", "ls-tree", "ls-remote", "rev-parse", "grep",
             "show-ref", "cat-file", "count-objects"}
_SYSTEMCTL_READ = {"status", "show", "cat", "is-active", "is-enabled", "is-failed",
                   "list-units", "list-unit-files", "list-timers", "list-sockets",
                   "list-dependencies", "get-default", "show-environment"}
_APT_READ = {"list", "search", "show", "policy", "showpkg", "depends", "rdepends",
             "madison", "changelog"}

# Anything containing one of these as a standalone word is mutating no matter
# what (covers xargs targets, subshells and pipelines cheaply).
_HARD_MUTATE = re.compile(
    r"(?:^|[\s;|&(`])("
    r"rm|mv|dd|mkfs|shred|truncate|shutdown|reboot|poweroff|halt|"
    r"kill|pkill|killall|useradd|userdel|usermod|groupadd|groupdel|chpasswd|"
    r"passwd|visudo|iptables|nft|ufw|tee|chmod|chown|chgrp|ln|mkdir|rmdir|"
    r"rsync|scp|sftp|umount|sysctl|modprobe|insmod|rmmod|update-grub|"
    r"mkswap|swapoff|swapon|parted|fdisk|sgdisk|"
    r"crontab|at)\b", re.I)

# stderr merges, null sinks and input redirects are fine;
# any remaining redirect writes a file.
_REDIRECT_OK = re.compile(r"(?:\d?>>?\s*(?:/dev/null|/dev/stderr|/dev/stdout)|\d?>&\d?|<)")
_REDIRECT = re.compile(r"\d?>>?")

# `VAR=value cmd` prefixes are only harmless for variables that cannot make the
# command run something else. LESSOPEN, PAGER, GIT_*, LD_PRELOAD, BASH_ENV and
# friends all can, so only this list is peeled off as a no-op.
_SAFE_ENV = re.compile(r"^(?:LANG|LANGUAGE|LC_[A-Z]+|TZ|COLUMNS|LINES|TERM|NO_COLOR|"
                       r"CLICOLOR(?:_FORCE)?|SYSTEMD_COLORS|SYSTEMD_LESS)=")
_SAFE_PAGER = re.compile(r"^(?:PAGER|GIT_PAGER|SYSTEMD_PAGER|MANPAGER)=(?:cat|''|\"\"|)$")

# Names that never leave the local network: mDNS, router and container suffixes.
_LOCAL_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".localhost",
                   ".localdomain")


def _tokens(segment: str) -> list[str]:
    return [t for t in re.split(r"\s+", segment.strip()) if t]


def _strip_wrappers(toks: list[str]) -> list[str] | None:
    """Peel harmless prefixes (sudo, nice, env, timeout N, LANG=x assignments).
    None means a prefix could change what runs (an unsafe VAR=x)."""
    while toks:
        t = toks[0]
        if t in ("sudo", "nice", "ionice", "nohup", "command", "builtin", "time", "env"):
            toks = toks[1:]
            while toks and toks[0].startswith("-"):
                toks = toks[1:]
            continue
        if t == "timeout":
            toks = toks[1:]
            while toks and (toks[0].startswith("-") or re.fullmatch(r"[\d.]+[smhd]?", toks[0])):
                toks = toks[1:]
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=[^\s]*", t):
            if not (_SAFE_ENV.match(t) or _SAFE_PAGER.match(t)):
                return None
            toks = toks[1:]
            continue
        break
    return toks


# ------------------------------------------------------------- destinations

def host_is_local(host: str) -> bool:
    """True if connecting to `host` cannot reach the public internet: loopback,
    private / link-local / CGNAT addresses, single-label names (Docker services,
    LAN hostnames) and the reserved local suffixes. Anything unclear is remote."""
    h = (host or "").strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    h = h.split("%", 1)[0].rstrip(".")
    if not h or re.search(r"[^a-z0-9._:-]", h):
        return False
    try:
        return not ipaddress.ip_address(h).is_global
    except ValueError:
        pass
    # curl and friends accept one-number IPv4 forms (2130706433, 0x7f000001,
    # 017700000001): judge those by the address they turn into, not as a name.
    if re.fullmatch(r"0x[0-9a-f]+|\d+", h):
        try:
            if h.startswith("0x"):
                value = int(h, 16)
            elif h.startswith("0") and len(h) > 1:
                value = int(h, 8)
            else:
                value = int(h)
            return value < 2 ** 32 and not ipaddress.IPv4Address(value).is_global
        except ValueError:
            return False
    if h == "localhost" or h.endswith(_LOCAL_SUFFIXES):
        return True
    return "." not in h and ":" not in h


def _target_host(token: str) -> str:
    """The host a URL / host[:port] / user@host:path argument points at."""
    t = token.strip()
    if "://" in t:
        try:
            return urlsplit(t).hostname or ""
        except ValueError:
            return ""
    t = t.split("/", 1)[0]
    if "@" in t:
        t = t.rsplit("@", 1)[1]
    if t.startswith("["):
        return t[1:t.find("]")] if "]" in t else ""
    if t.count(":") == 1:
        t = t.split(":", 1)[0]
    return t


def url_is_local(url: str) -> bool:
    """For fetch_url: an http(s) URL whose host stays on this network."""
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        return False
    return host_is_local(_target_host(url))


def _all_local(targets: list[str]) -> bool:
    return bool(targets) and all(host_is_local(_target_host(t)) for t in targets)


def _positional(args: list[str], takes_value: set[str]) -> list[str] | None:
    """Split shell words into positional arguments, skipping option values.
    Returns None for something it cannot follow (an option missing its value)."""
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a == "--":
            out.extend(args[i + 1:])
            break
        if a.startswith("--"):
            name = a.split("=", 1)[0]
            if name in takes_value and "=" not in a:
                if i + 1 >= len(args):
                    return None
                i += 1
        elif a.startswith("-") and len(a) > 1:
            # a short option cluster (-sSL, -qO-): the first letter that takes a
            # value swallows the rest of the cluster, or the next word
            for j, ch in enumerate(a[1:], start=1):
                if "-" + ch in takes_value:
                    if j == len(a) - 1:
                        if i + 1 >= len(args):
                            return None
                        i += 1
                    break
        else:
            out.append(a)
        i += 1
    return out


def _flag_values(args: list[str], names: set[str]) -> list[str]:
    """Every value given to one of `names` (long `--x=v` / `--x v`, short `-xv` / `-x v`)."""
    vals = []
    for i, a in enumerate(args):
        for n in names:
            if a == n:
                vals.append(args[i + 1] if i + 1 < len(args) else "")
            elif n.startswith("--") and a.startswith(n + "="):
                vals.append(a.split("=", 1)[1])
            elif not n.startswith("--") and a.startswith(n) and len(a) > len(n) \
                    and not a.startswith("--"):
                vals.append(a[len(n):])
    return vals


def _cluster_values(args: list[str], letters: str) -> list[str]:
    """Values of single-letter options, also inside clusters: `-qO-` gives
    O="-", `-sO file` gives O="file"."""
    vals = []
    for i, a in enumerate(args):
        if not a.startswith("-") or a.startswith("--") or len(a) < 2:
            continue
        for j, ch in enumerate(a[1:], start=1):
            if ch in letters:
                rest = a[j + 1:]
                vals.append(rest if rest else (args[i + 1] if i + 1 < len(args) else ""))
                break
    return vals


def _has_flag(args: list[str], names: set[str]) -> bool:
    """Exact flags or their `--x=value` form (long) / clustered letter (short)."""
    for a in args:
        if a in names:
            return True
        if a.startswith("--") and a.split("=", 1)[0] in names:
            return True
        if a.startswith("-") and not a.startswith("--") and len(a) > 2:
            if any("-" + ch in names for ch in a[1:]):
                return True
    return False


_CURL_VALUE = {
    "-A", "-b", "-c", "-C", "-d", "-D", "-e", "-E", "-F", "-H", "-K", "-m", "-o",
    "-P", "-Q", "-r", "-T", "-u", "-U", "-w", "-x", "-X", "-y", "-Y", "-z",
    "--cacert", "--capath", "--cert", "--cert-type", "--ciphers", "--config",
    "--connect-timeout", "--connect-to", "--continue-at", "--cookie", "--cookie-jar",
    "--data", "--data-ascii", "--data-binary", "--data-raw", "--data-urlencode",
    "--dns-servers", "--doh-url", "--dump-header", "--etag-save", "--form",
    "--form-string", "--header", "--hsts", "--interface", "--json", "--key",
    "--libcurl", "--limit-rate", "--max-filesize", "--max-redirs", "--max-time",
    "--noproxy", "--oauth2-bearer", "--output", "--output-dir", "--pass",
    "--pinnedpubkey", "--preproxy", "--proxy", "--proxy-header", "--proxy-user",
    "--range", "--referer", "--request", "--request-target", "--resolve", "--retry",
    "--retry-delay", "--retry-max-time", "--socks4", "--socks4a", "--socks5",
    "--socks5-hostname", "--stderr", "--trace", "--trace-ascii", "--unix-socket",
    "--upload-file", "--url", "--url-query", "--user", "--user-agent",
    "--variable", "--write-out", "--alt-svc", "--abstract-unix-socket",
}
# writes a file, sends data, reads options from elsewhere, or reroutes the
# connection somewhere other than the URL says
_CURL_BAD = {
    "-O", "--remote-name", "--remote-name-all", "--output-dir",
    "-c", "--cookie-jar", "--libcurl", "--etag-save", "--hsts", "--alt-svc",
    "-T", "--upload-file", "-F", "--form", "--form-string", "-d", "--data",
    "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode", "--json",
    "-K", "--config", "--variable",
    "-x", "--proxy", "--preproxy", "--socks4", "--socks4a", "--socks5",
    "--socks5-hostname", "--connect-to", "--resolve", "--doh-url", "--dns-servers",
}
_WGET_VALUE = {
    "-O", "-o", "-a", "-P", "-e", "-i", "-B", "-t", "-T", "-w", "-Q", "-U", "-l",
    "-A", "-R", "-D", "-X", "-I",
    "--output-document", "--output-file", "--append-output", "--directory-prefix",
    "--execute", "--input-file", "--base", "--tries", "--timeout", "--wait",
    "--quota", "--user-agent", "--level", "--accept", "--reject", "--domains",
    "--exclude-directories", "--include-directories", "--header", "--user",
    "--password", "--http-user", "--http-password", "--post-data", "--post-file",
    "--body-data", "--body-file", "--method", "--referer", "--load-cookies",
    "--save-cookies", "--ca-certificate", "--certificate", "--private-key",
    "--bind-address", "--limit-rate", "--dns-servers", "--config",
}
_WGET_BAD = {
    "-o", "--output-file", "-a", "--append-output", "-P", "--directory-prefix",
    "-e", "--execute", "-i", "--input-file", "--post-data", "--post-file",
    "--body-data", "--body-file", "--save-cookies", "--config",
    "-r", "--recursive", "-m", "--mirror", "-N", "--timestamping",
    "-x", "--force-directories", "--dns-servers",
}
_DIG_VALUE = {"-b", "-c", "-f", "-k", "-p", "-q", "-t", "-x", "-y"}
_PING_VALUE = {"-c", "-i", "-I", "-s", "-t", "-W", "-w", "-p", "-Q", "-M", "-l", "-S", "-F"}
_TRACE_VALUE = {"-f", "-g", "-i", "-m", "-N", "-p", "-q", "-s", "-t", "-w", "-z", "-l", "-b"}


def _method_is_read(args: list[str]) -> bool:
    methods = _flag_values(args, {"-X", "--request", "--method"})
    return all(m.strip("'\"").upper() in ("GET", "HEAD") for m in methods)


def _network_read_only(cmd: str, raw: str) -> bool:
    """A network command is read-only only if it writes nothing, sends no data
    and every destination is local. `raw` is the segment as written: any `$` or
    backtick in it means a destination or payload built at run time, which can
    never be judged in advance."""
    if "$" in raw or "`" in raw:
        return False
    try:
        words = shlex.split(raw, posix=True)
    except ValueError:
        return False
    words = _strip_wrappers(words)
    if not words:
        return False
    args = words[1:]

    if cmd == "curl":
        if _has_flag(args, _CURL_BAD) or not _method_is_read(args):
            return False
        dumps = _flag_values(args, {"-D", "--dump-header", "--trace", "--trace-ascii",
                                    "--stderr"})
        if any(v != "-" for v in dumps):
            return False
        # -o / --output only to stdout or /dev/null (`-o /dev/null -w '%{http_code}'`)
        outs = _cluster_values(args, "o") + _flag_values(args, {"--output"})
        if any(v not in ("-", "/dev/null") for v in outs):
            return False
        targets = (_positional(args, _CURL_VALUE) or []) + _flag_values(args, {"--url"})
        return _all_local(targets)
    if cmd == "wget":
        if _has_flag(args, _WGET_BAD) or not _method_is_read(args):
            return False
        outs = _cluster_values(args, "O") + _flag_values(args, {"--output-document"})
        # wget saves a file unless told to print (-O -) or only check (--spider)
        if not (outs and all(v == "-" for v in outs)) and "--spider" not in args:
            return False
        return _all_local(_positional(args, _WGET_VALUE) or [])
    if cmd in ("http", "https"):
        # HTTPie: [METHOD] URL [request items]; any item is data
        if _has_flag(args, {"-d", "--download", "-o", "--output", "-f", "--form",
                            "--offline", "--session"}):
            return False
        pos = [a for a in args if not a.startswith("-")]
        if pos and pos[0].upper() in ("GET", "HEAD"):
            pos = pos[1:]
        elif pos and re.fullmatch(r"[A-Z]+", pos[0]):
            return False
        return len(pos) == 1 and _all_local(pos)
    if cmd == "dig":
        if _has_flag(args, {"-f", "-k", "-y"}):
            return False
        pos = _positional(args, _DIG_VALUE)
        if pos is None:
            return False
        targets = [a[1:] if a.startswith("@") else a for a in pos if not a.startswith("+")]
        targets += _flag_values(args, {"-q", "-x"})
        return _all_local(targets)
    if cmd in ("nslookup", "host"):
        pos = _positional(args, {"-t", "-W", "-R", "-N", "-m", "-c"})
        return pos is not None and _all_local(pos)
    if cmd in ("ping", "ping6"):
        # without a count ping streams until the tool timeout kills it
        if not _has_flag(args, {"-c"}):
            return False
        pos = _positional(args, _PING_VALUE)
        return pos is not None and _all_local(pos)
    if cmd in ("traceroute", "traceroute6", "tracepath", "tracepath6"):
        pos = _positional(args, _TRACE_VALUE)
        # traceroute's optional second word is a packet length, not a host
        hosts = [p for p in (pos or []) if not p.isdigit()]
        return pos is not None and _all_local(hosts)
    return False


# ---------------------------------------------------- conditional read-only

_SED_S = re.compile(r"s([^\\\n])((?:\\.|(?!\1)[^\n])*)\1((?:\\.|(?!\1)[^\n])*)\1([^;\n}]*)")
_SED_Y = re.compile(r"y([^\\\n])(?:\\.|(?!\1)[^\n])*\1(?:\\.|(?!\1)[^\n])*\1")
_SED_ADDR = re.compile(r"/(?:\\.|[^/\n])*/[IM]*|\\([^\\\n])(?:\\.|(?!\1)[^\n])*\1[IM]*")


def _sed_script_safe(script: str) -> bool:
    """False if a sed script can run a program (`e`, `s///e`) or write a file
    (`w`, `W`, `s///w`). Conservative: a leftover e/w/W anywhere counts."""
    for m in _SED_S.finditer(script):
        if re.search(r"[ew]", m.group(4)):
            return False
    rest = _SED_S.sub(";", script)
    rest = _SED_Y.sub(";", rest)
    rest = _SED_ADDR.sub(" ", rest)
    return not re.search(r"[ewW]", rest)


def _sed_read_only(words: list[str]) -> bool:
    args = words[1:]
    if "--sandbox" in args:     # GNU sed then refuses e, w and r outright
        return not any(a.startswith("--in-place") or (a.startswith("-") and not
                       a.startswith("--") and "i" in a[1:]) for a in args)
    scripts, positional_script, i = [], False, 0
    while i < len(args):
        a = args[i]
        if a.startswith("--in-place") or a in ("-f", "--file") or a.startswith("--file="):
            return False
        if a in ("-e", "--expression"):
            if i + 1 >= len(args):
                return False
            scripts.append(args[i + 1])
            positional_script = True
            i += 2
            continue
        if a.startswith("--expression="):
            scripts.append(a.split("=", 1)[1])
            positional_script = True
        elif a in ("-l", "--line-length"):
            i += 1
        elif a.startswith("-") and not a.startswith("--") and len(a) > 1:
            cluster = a[1:]
            if "i" in cluster or "f" in cluster:
                return False
            if "e" in cluster:
                rest = cluster[cluster.index("e") + 1:]
                if rest:
                    scripts.append(rest)
                elif i + 1 < len(args):
                    scripts.append(args[i + 1])
                    i += 1
                else:
                    return False
                positional_script = True
        elif not a.startswith("-") and not positional_script:
            scripts.append(a)
            positional_script = True
        i += 1
    return bool(scripts) and all(_sed_script_safe(s) for s in scripts)


def _conditional_read_only(cmd: str, raw: str, toks: list[str]) -> bool:
    try:
        words = shlex.split(raw, posix=True)
    except ValueError:
        return False
    words = _strip_wrappers(words) or []
    args = words[1:] if words else toks[1:]
    short = [a[1:] for a in args if a.startswith("-") and not a.startswith("--") and len(a) > 1]

    if cmd == "sort":
        # -o / --output write a file; --compress-program launches one
        return not (any("o" in c for c in short)
                    or any(a.startswith(("--output", "--compress-program")) for a in args))
    if cmd == "uniq":
        pos = _positional(args, {"-f", "-s", "-w", "--skip-fields", "--skip-chars",
                                 "--check-chars"})
        if pos is None:
            return False
        return len(pos) <= 1          # a second file name is the OUTPUT file
    if cmd == "date":
        for c in short:
            for ch in c:
                if ch in "Idfr":
                    break             # the rest of the cluster is a value
                if ch == "s":
                    return False      # -s / --set sets the clock
        if any(a.startswith("--set") for a in args):
            return False
        pos = _positional(args, {"-d", "--date", "-f", "--file", "-r", "--reference"})
        # date MMDDhhmm… sets the clock; only +FORMAT is a display argument
        return pos is not None and all(p.startswith("+") for p in pos)
    if cmd == "hostname":
        if any(set(c) & set("Fb") for c in short) or \
                any(a.startswith(("--file", "--boot")) for a in args):
            return False
        return not [a for a in args if not a.startswith("-")]
    if cmd == "dmesg":
        # clear, read-and-clear, console on/off/level all change kernel state
        return not (any(set(c) & set("cCDEn") for c in short) or
                    any(a.split("=", 1)[0] in ("--clear", "--read-clear", "--console-off",
                                               "--console-on", "--console-level")
                        for a in args))
    if cmd == "xxd":
        value_opts = {"-c", "-cols", "-g", "-groupsize", "-l", "-len", "-o", "-offset",
                      "-s", "-seek", "-n", "-name"}
        pos, i = [], 0
        while i < len(args):
            if args[i] in value_opts:
                i += 2
                continue
            if not args[i].startswith("-") or args[i] == "-":
                pos.append(args[i])
            i += 1
        return len(pos) <= 1          # xxd IN OUT writes OUT
    if cmd == "tree":
        return not any(a.startswith("-o") for a in args)
    if cmd in ("less", "more"):
        # -o/-O log the input to a file; +cmd runs less commands (incl. !shell)
        return not any(a.startswith(("-o", "-O", "--log-file", "--LOG-FILE", "+"))
                       for a in args)
    if cmd == "file":
        return not any(a.startswith("--compile") or
                       (a.startswith("-") and not a.startswith("--") and "C" in a[1:])
                       for a in args)
    if cmd == "rg":
        return not any(a.startswith("--pre") for a in args)   # --pre runs a program
    if cmd == "lastlog":
        return not (any(set(c) & set("CS") for c in short) or
                    any(a.startswith(("--clear", "--set")) for a in args))
    if cmd == "getent":
        if "$" in raw or "`" in raw:
            return False
        pos = [a for a in args if not a.startswith("-")]
        if pos and pos[0] in ("hosts", "ahosts", "ahostsv4", "ahostsv6"):
            # a lookup of a name is a DNS query to whoever serves that name
            return all(host_is_local(n) for n in pos[1:])
        return True
    return False


def _git_read_only(rest: list[str], raw: str) -> bool:
    garg = list(rest)
    while garg and garg[0].startswith("-"):
        g = garg[0]
        # -c / --config-env / --exec-path=… change which programs git runs
        # (core.pager, core.fsmonitor, aliases): never auto-run those
        if g in ("-c", "--config-env") or g.startswith(("--config-env=", "--exec-path=")):
            return False
        two = g in ("-C", "--git-dir", "--work-tree", "--namespace")
        garg = garg[2:] if two and len(garg) > 1 else garg[1:]
    args = [t for t in garg if not t.startswith("-")]
    flags = [t for t in garg if t.startswith("-")]
    sub = args[0].lower() if args else ""
    # --output writes a file; grep -O / --open-files-in-pager launches a program
    if any(f.startswith("--output") for f in flags):
        return False
    if sub == "grep" and any(f.startswith(("-O", "--open-files-in-pager")) for f in flags):
        return False
    if sub == "ls-remote":
        if any(f.startswith(("--upload-pack", "-u", "--exec")) for f in flags):
            return False
        repo = args[1] if len(args) > 1 else ""
        looks_remote = "://" in repo or re.match(r"^[^/\s]+@[^:/\s]+:", repo or "")
        if looks_remote:
            return "$" not in raw and "`" not in raw and host_is_local(_target_host(repo))
        return True
    if sub in _GIT_READ:
        return True
    # branch/tag/stash/remote/config are read-only only in their list forms
    if sub in ("branch", "tag", "remote") and len(args) == 1:
        return True
    if sub == "stash" and args[1:2] == ["list"]:
        return True
    if sub == "config" and ({"--list", "--get", "-l"} & set(flags)):
        return True
    return False


_IP_READ_CMDS = {"show", "list", "lst", "ls", "sh", "s", "l", "get", "help"}


def _ip_read_only(args: list[str]) -> bool:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        opt = args[i].lstrip("-")
        if opt in ("b", "batch"):
            return False                     # runs ip commands from a file
        i += 2 if opt in ("n", "netns", "rc", "rcvbuf") else 1
    rest = [a.lower() for a in args[i:]]
    if not rest:
        return False
    if len(rest) == 1:
        return True                          # `ip a`, `ip route`, `ip link`
    obj, verb = rest[0], rest[1]
    if obj == "netns" and verb in ("exec", "add", "delete", "del", "set", "attach"):
        return False
    return verb in _IP_READ_CMDS or obj in ("monitor", "mon", "m")


# --------------------------------------------------------------- core

_SUBST = re.compile(r"\$\(([^()]*)\)|`([^`]*)`|[<>]\(([^()]*)\)")


def _segment_read_only(segment: str) -> bool:
    segment = segment.strip()
    if not segment:
        return True
    if _HARD_MUTATE.search(segment):
        return False
    if _REDIRECT.search(_REDIRECT_OK.sub(" ", re.sub(r"[<>]\(", "(", segment))):
        return False
    # classify command / process substitutions innermost-first, then the
    # outer command; anything left that cannot be unwrapped is not judged
    outer = segment
    for _ in range(16):
        m = _SUBST.search(outer)
        if not m:
            break
        inner = next(g for g in m.groups() if g is not None)
        if inner.strip() and not is_read_only(inner):
            return False
        outer = outer[:m.start()] + "X" + outer[m.end():]
    if "$(" in outer or "`" in outer or "<(" in outer or ">(" in outer:
        return False
    toks = _strip_wrappers(_tokens(outer))
    if toks is None:
        return False
    if not toks:
        return True
    cmd = toks[0].rsplit("/", 1)[-1].lower()
    rest = toks[1:]
    flags = {t for t in rest if t.startswith("-")}
    args = [t for t in rest if not t.startswith("-")]

    if cmd in _ALWAYS_READ:
        return True
    if cmd in _CONDITIONAL_READ:
        return _conditional_read_only(cmd, outer, toks)
    if cmd in _NETWORK:
        return _network_read_only(cmd, segment)
    if cmd == "find":
        return not ({"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint",
                     "-fprint0", "-fprintf", "-fls"} & set(rest))
    if cmd == "sed":
        try:
            words = shlex.split(outer, posix=True)
        except ValueError:
            return False
        words = _strip_wrappers(words)
        return bool(words) and _sed_read_only(words)
    if cmd == "xargs":
        inner = list(rest)
        while inner and inner[0].startswith("-"):
            two = inner[0] in ("-I", "-n", "-P", "-L", "-d", "-s", "-a",
                               "--max-args", "--max-procs", "--delimiter")
            inner = inner[2:] if two and len(inner) > 1 else inner[1:]
        return is_read_only(" ".join(inner)) if inner else True
    if cmd == "docker":
        sub = [a.lower() for a in args[:2]]
        if not sub:
            return False
        if sub[0] == "exec":
            # exec is fine when the command *inside* the container is read-only
            inner = list(rest)
            while inner and inner[0] != "exec":
                inner = inner[1:]
            inner = inner[1:]
            while inner and inner[0].startswith("-"):
                two = inner[0] in ("-e", "--env", "-u", "--user", "-w", "--workdir",
                                   "--env-file", "--detach-keys")
                inner = inner[2:] if two and len(inner) > 1 else inner[1:]
            inner = inner[1:]  # drop the container name
            return bool(inner) and is_read_only(" ".join(inner))
        if len(sub) >= 2 and (sub[0], sub[1]) in _DOCKER_READ_PAIRS:
            return True
        if sub[0] in _DOCKER_READ_SUB:
            # `docker stats` without --no-stream streams forever
            return sub[0] != "stats" or "--no-stream" in flags
        return False
    if cmd == "git":
        return _git_read_only(rest, segment)
    if cmd == "systemctl":
        sub = next((a.lower() for a in args), "list-units")
        return sub in _SYSTEMCTL_READ
    if cmd == "journalctl":
        return not any(f.startswith("--vacuum") or f.split("=", 1)[0] in (
            "--rotate", "--flush", "--sync", "--relinquish-var", "--smart-relinquish-var",
            "--setup-keys", "--update-catalog") for f in flags)
    if cmd in ("apt", "apt-get", "apt-cache"):
        return bool(args) and args[0].lower() in _APT_READ
    if cmd in ("dpkg", "dpkg-query"):
        return cmd == "dpkg-query" or bool(
            {"-l", "--list", "-L", "--listfiles", "-s", "--status",
             "-S", "--search", "-p", "--print-avail"} & flags)
    if cmd == "ip":
        return _ip_read_only(rest)
    if cmd == "mount":
        return not rest
    if cmd in ("bash", "sh", "zsh", "dash"):
        m = re.search(r"(?:^|\s)-\w*c\s+(['\"])(.*)\1", outer, re.S)
        return bool(m) and is_read_only(m.group(2))
    if cmd == "pocketadm":
        sub = (args[0].lower() if args else "")
        if sub in _POCKETADM_READ:
            return True             # its records and notes; notes are not the server
        if sub == "host":
            # the shell splits the words, the CLI joins them with spaces and
            # hands that to bash -c on the host: judge exactly that string
            try:
                words = _strip_wrappers(shlex.split(outer, posix=True)) or []
            except ValueError:
                return False
            inner = words[words.index("host") + 1:] if "host" in words else []
            while inner and inner[0].startswith("--timeout"):
                inner = inner[2:] if inner[0] == "--timeout" else inner[1:]
            return bool(inner) and is_read_only(" ".join(inner))
        return False
    return False


# `pocketadm` subcommands that only read (records, notes, the map) or touch
# the assistant's own notes — see server/cli.py
_POCKETADM_READ = {"overview", "updates", "activity", "audit", "health", "metrics", "storage",
                   "jobs", "watch", "notes", "map", "remember", "forget", "-h", "--help"}


def is_read_only(command: str) -> bool:
    """True if every part of the (possibly compound) command is read-only."""
    if not command or not isinstance(command, str) or len(command) > 4000:
        return False
    # split on connectors outside quotes — a cheap state machine
    segments, buf, q, i = [], [], "", 0
    while i < len(command):
        ch = command[i]
        if q:
            if ch == q and (i == 0 or command[i - 1] != "\\"):
                q = ""
            buf.append(ch)
        elif ch in "'\"":
            q = ch
            buf.append(ch)
        elif ch == "&" and buf and buf[-1] == ">":
            buf.append(ch)          # ">&" is a redirect, not a control operator
        elif ch == "&" and i + 1 < len(command) and command[i + 1] == ">":
            buf.append(ch)          # "&>" redirects both streams
        elif ch in ";|&\n":
            segments.append("".join(buf))
            buf = []
            if ch in "|&" and i + 1 < len(command) and command[i + 1] == ch:
                i += 1
            elif ch == "|" and i + 1 < len(command) and command[i + 1] == "&":
                i += 1              # "|&" pipes stdout+stderr
        else:
            buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return all(_segment_read_only(s) for s in segments)


# ------------------------------------------------------ protected files

# PocketADM's own credentials and the coding CLIs' logins. The agent has no
# reason to read them, and a model steered by something it read would want
# exactly these. Reading them is never automatic (a tap shows the command).
_PROTECTED_RE = re.compile(
    r"(?:^|[\s/'\"=])(?:secret\.key|admin\.pw|login_attempts\.json|"
    r"\.credentials\.json)\b|"
    r"(?:^|[\s'\"=])/data/settings\.json|helmsman_data/_data/settings\.json|"
    r"\.codex/auth\.json|\.claude/\.credentials")


def touches_protected(text: str) -> bool:
    """True if a command or path names one of PocketADM's own secrets."""
    return isinstance(text, str) and bool(_PROTECTED_RE.search(text))
