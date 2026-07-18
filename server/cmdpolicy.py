"""Classify shell commands as read-only or mutating.

Used to let the agent run harmless inspection commands (docker ps, ls, df …)
without a per-action approval tap, while anything that writes, deletes or
changes state still asks first. The classifier is deliberately conservative:
everything it does not positively recognise as read-only counts as mutating —
a false "mutate" only costs the user a tap, a false "read" would skip one.
"""

from __future__ import annotations

import re

# Commands that never change server state, regardless of arguments
# (writing anywhere would need a shell redirect, which is checked separately).
_ALWAYS_READ = {
    "ls", "dir", "tree", "cat", "head", "tail", "wc", "cut", "sort", "uniq",
    "tr", "column", "diff", "cmp", "strings", "file", "stat", "readlink",
    "basename", "dirname", "realpath", "pwd", "echo", "printf", "true", "false",
    "test", "[", "which", "whereis", "type",
    "grep", "egrep", "fgrep", "zgrep", "rg", "ag",
    "du", "df", "free", "uptime", "date", "cal", "hostname", "uname", "arch",
    "whoami", "id", "groups", "last", "lastlog", "w", "who", "users",
    "ps", "pgrep", "pstree", "lsof", "vmstat", "iostat", "mpstat", "nproc",
    "lscpu", "lsmem", "lsblk", "lsusb", "lspci", "blkid", "dmesg",
    "ss", "netstat", "dig", "nslookup", "host", "traceroute", "tracepath",
    "md5sum", "sha1sum", "sha256sum", "sha512sum", "cksum", "b2sum",
    "printenv", "locale", "getent", "jq", "xxd", "od",
    "less", "more", "nl", "tac", "zcat", "seq", "expr", "sleep",
}

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


def _tokens(segment: str) -> list[str]:
    return [t for t in re.split(r"\s+", segment.strip()) if t]


def _strip_wrappers(toks: list[str]) -> list[str]:
    """Peel harmless prefixes (sudo, nice, env, timeout N, VAR=x assignments)."""
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
            toks = toks[1:]
            continue
        break
    return toks


def _segment_read_only(segment: str) -> bool:
    segment = segment.strip()
    if not segment:
        return True
    if _HARD_MUTATE.search(segment):
        return False
    if _REDIRECT.search(_REDIRECT_OK.sub(" ", segment)):
        return False
    # classify command substitutions recursively, then judge the outer command
    for pair in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", segment):
        for part in pair:
            if part and not is_read_only(part):
                return False
    outer = re.sub(r"\$\([^()]*\)|`[^`]*`", "X", segment)
    toks = _strip_wrappers(_tokens(outer))
    if not toks:
        return True
    cmd = toks[0].rsplit("/", 1)[-1].lower()
    rest = toks[1:]
    flags = {t for t in rest if t.startswith("-")}
    args = [t for t in rest if not t.startswith("-")]

    if cmd in _ALWAYS_READ:
        return True
    if cmd == "find":
        return not ({"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint",
                     "-fprintf", "-fls"} & set(rest))
    if cmd == "sed":
        return not any(f == "--in-place" or f.startswith("-i") for f in flags)
    if cmd == "xargs":
        inner = list(rest)
        while inner and inner[0].startswith("-"):
            two = inner[0] in ("-I", "-n", "-P", "-L", "-d", "-s", "-a",
                               "--max-args", "--max-procs", "--delimiter")
            inner = inner[2:] if two and len(inner) > 1 else inner[1:]
        return is_read_only(" ".join(inner)) if inner else True
    if cmd in ("curl", "wget", "http", "https"):
        bad = {"-o", "-O", "--output", "--remote-name", "-T", "--upload-file",
               "-F", "--form", "-d", "--data", "--data-raw", "--data-binary",
               "--data-urlencode", "--json", "-X", "--request", "--method"}
        if not (bad & flags):
            return True
        m = re.search(r"(?:-X|--request|--method)[= ]+['\"]?(\w+)", outer, re.I)
        return bool(m and m.group(1).upper() in ("GET", "HEAD")) \
            and not ((bad - {"-X", "--request", "--method"}) & flags)
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
                two = inner[0] in ("-e", "--env", "-u", "--user", "-w", "--workdir")
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
        # skip global options that take a value (git -C <dir> -c a=b <sub> …)
        garg = list(rest)
        while garg and garg[0].startswith("-"):
            two = garg[0] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace")
            garg = garg[2:] if two and len(garg) > 1 else garg[1:]
        args = [t for t in garg if not t.startswith("-")]
        sub = args[0].lower() if args else ""
        if sub in _GIT_READ:
            return True
        # branch/tag/stash/remote/config are read-only only in their list forms
        if sub in ("branch", "tag", "remote") and len(args) == 1:
            return True
        if sub == "stash" and args[1:2] == ["list"]:
            return True
        if sub == "config" and ({"--list", "--get", "-l"} & flags):
            return True
        return False
    if cmd == "systemctl":
        sub = next((a.lower() for a in args), "list-units")
        return sub in _SYSTEMCTL_READ
    if cmd == "journalctl":
        return not any(f.startswith("--vacuum") or f in ("--rotate", "--flush", "--sync")
                       for f in flags)
    if cmd in ("apt", "apt-get", "apt-cache"):
        return bool(args) and args[0].lower() in _APT_READ
    if cmd in ("dpkg", "dpkg-query"):
        return cmd == "dpkg-query" or bool(
            {"-l", "--list", "-L", "--listfiles", "-s", "--status",
             "-S", "--search", "-p", "--print-avail"} & flags)
    if cmd == "ip":
        r = " ".join(a.lower() for a in args)
        return any(w in r for w in ("show", "list")) or r in (
            "a", "addr", "r", "route", "link", "neigh")
    if cmd == "ping":
        return any(f == "-c" or f.startswith("-c") for f in flags)
    if cmd == "mount":
        return not rest
    if cmd in ("bash", "sh", "zsh", "dash"):
        m = re.search(r"(?:^|\s)-\w*c\s+(['\"])(.*)\1", outer, re.S)
        return bool(m) and is_read_only(m.group(2))
    return False


def is_read_only(command: str) -> bool:
    """True if every part of the (possibly compound) command is read-only."""
    if not command or len(command) > 4000:
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
