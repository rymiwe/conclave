#!/usr/bin/env bash
# HOME repair: a Windows-style or missing HOME becomes the account record's home.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
want="$(getent passwd "$(id -un)" | cut -d: -f6)"
fail=0
for bad in 'C:Usersryanw' '' 'relative/dir' '/no/such/dir'; do
  got="$(HOME="$bad" bash -c ". '$here/home-env.sh' 2>/dev/null; printf %s \"\$HOME\"")"
  if [ "$got" = "$want" ]; then echo "ok HOME='$bad' -> $got"; else echo "FAIL HOME='$bad' -> $got"; fail=1; fi
done
got="$(HOME="$want" bash -c ". '$here/home-env.sh'; printf %s \"\$HOME\"" 2>&1)"
[ "$got" = "$want" ] && echo "ok a good HOME is left alone, silently" || { echo "FAIL good HOME -> $got"; fail=1; }
got="$(HOME='C:Usersryanw' bash "$here/resolve-models.sh" 2>&1 >/dev/null | grep -c 'using')"
[ "$got" -ge 1 ] && echo "ok resolve-models.sh repairs HOME" || { echo "FAIL resolve-models.sh"; fail=1; }
[ "$fail" -eq 0 ] && echo ok
exit "$fail"
