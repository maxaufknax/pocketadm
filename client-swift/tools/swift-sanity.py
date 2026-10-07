#!/usr/bin/env python3
"""Grobe Syntaxprüfung für Swift-Dateien — ohne Compiler.

Auf dem Homeserver gibt es keinen Swift-Compiler; gebaut wird erst bei
Codemagic. Dieses Skript fängt die Fehlerklassen ab, die einen Build-Durchlauf
sonst für eine Kleinigkeit verbrennen: unausgeglichene Klammern, offene
String-Literale, kaputte Interpolation.

    ./tools/swift-sanity.py App
"""
import sys
from pathlib import Path

PAIRS = {'(': ')', '[': ']', '{': '}'}
CLOSERS = {v: k for k, v in PAIRS.items()}


def check(path: Path):
    """Zerlegt die Datei zeichenweise in Code, Strings und Kommentare und
    zählt dabei die Klammern. Gibt eine Liste von Befunden zurück."""
    src = path.read_text(encoding='utf-8')
    problems = []
    stack = []          # offene Klammern als (zeichen, zeile)
    i, line = 0, 1
    n = len(src)
    block_depth = 0     # Verschachtelungstiefe von /* */
    # Zustände für Strings: (art, interpolationstiefe)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ''

        if c == '\n':
            line += 1
            i += 1
            continue

        if block_depth:
            if c == '/' and nxt == '*':
                block_depth += 1
                i += 2
                continue
            if c == '*' and nxt == '/':
                block_depth -= 1
                i += 2
                continue
            i += 1
            continue

        # Zeilenkommentar
        if c == '/' and nxt == '/':
            while i < n and src[i] != '\n':
                i += 1
            continue

        # Blockkommentar
        if c == '/' and nxt == '*':
            block_depth = 1
            i += 2
            continue

        # Raw-String #"..."# und ###"""..."""###
        if c == '#':
            hashes = 0
            j = i
            while j < n and src[j] == '#':
                hashes += 1
                j += 1
            if j < n and src[j] == '"':
                end = _skip_string(src, j, hashes)
                if end is None:
                    problems.append((line, 'nicht geschlossenes Raw-String-Literal'))
                    break
                line += src.count('\n', i, end)
                i = end
                continue
            i = j
            continue

        if c == '"':
            end = _skip_string(src, i, 0)
            if end is None:
                problems.append((line, 'nicht geschlossenes String-Literal'))
                break
            line += src.count('\n', i, end)
            i = end
            continue

        if c in PAIRS:
            stack.append((c, line))
        elif c in CLOSERS:
            if not stack:
                problems.append((line, f'schließende {c!r} ohne Gegenstück'))
            elif stack[-1][0] != CLOSERS[c]:
                opener, opened_at = stack[-1]
                problems.append(
                    (line, f'{c!r} schließt {opener!r} aus Zeile {opened_at}'))
                stack.pop()
            else:
                stack.pop()
        i += 1

    if block_depth:
        problems.append((line, 'nicht geschlossener Blockkommentar /*'))
    for opener, opened_at in stack:
        problems.append((opened_at, f'{opener!r} wird nie geschlossen'))
    return problems


def _skip_string(src, start, hashes):
    """Springt hinter ein String-Literal. `start` zeigt auf das erste '"'.

    Interpolationen werden mitgelesen, damit Klammern darin nicht als Code
    gezählt werden. Gibt den Index hinter dem Literal zurück oder None.
    """
    n = len(src)
    delim = '"""' if src.startswith('"""', start) else '"'
    i = start + len(delim)
    closing = delim + '#' * hashes
    escape = '\\' + '#' * hashes
    while i < n:
        if src.startswith(escape + '(', i):
            # Interpolation: bis zur passenden schließenden Klammer springen
            depth = 0
            i += len(escape)
            while i < n:
                if src[i] == '(':
                    depth += 1
                elif src[i] == ')':
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                elif src[i] == '"':
                    inner = _skip_string(src, i, 0)
                    if inner is None:
                        return None
                    i = inner
                    continue
                i += 1
            continue
        if src.startswith(escape, i):
            i += len(escape) + 1
            continue
        if src.startswith(closing, i):
            return i + len(closing)
        if delim == '"' and src[i] == '\n':
            return None  # einzeiliges Literal über den Zeilenrand hinaus
        i += 1
    return None


def main():
    roots = [Path(a) for a in sys.argv[1:]] or [Path('App')]
    files = sorted(f for root in roots
                   for f in (root.rglob('*.swift') if root.is_dir() else [root]))
    if not files:
        print('Keine Swift-Dateien gefunden.')
        return 1

    failed = 0
    for f in files:
        problems = check(f)
        if problems:
            failed += 1
            for line, message in problems:
                print(f'{f}:{line}: {message}')
    print(f'\n{len(files)} Dateien geprüft, {failed} mit Befund.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
