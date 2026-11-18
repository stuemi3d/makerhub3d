#!/usr/bin/env bash
# Switches the app on the server to the test image of a pull request, or back to "latest".
#
#   ./scripts/testen.sh 198       test pull request 198  (image tag pr-198)
#   ./scripts/testen.sh latest    back to the normal version (also without an argument)
#
# Run it on the server; the folder above "scripts/" is the one with compose.yml and .env.
# The script only changes the tag in the MAKERHUB_IMAGE line of .env, then pulls and restarts.
# If the pull fails, nothing is restarted and .env stays as it was. (Issue #198)
set -eu

fail() {
  echo "Fehler: $1" >&2
  exit 1
}

if [ "$#" -gt 1 ]; then
  fail "Aufruf: ./scripts/testen.sh NUMMER  oder  ./scripts/testen.sh latest"
fi
if [ "$#" -eq 0 ]; then
  wanted=latest
else
  wanted=$1
fi
case $wanted in
  latest) tag=latest ;;
  *)
    if printf '%s' "$wanted" | grep -Eq '^[0-9]{1,9}$'; then
      tag="pr-$wanted"
    else
      fail "Erlaubt sind nur eine Nummer (z. B. 198) oder latest. Nichts geändert."
    fi
    ;;
esac

cd "$(dirname "$0")/.."
[ -f .env ] || fail "Keine .env im Ordner $(pwd). Nichts geändert."

# The image name is taken from the active MAKERHUB_IMAGE line (without the tag).
current=$(grep -E '^MAKERHUB_IMAGE=' .env | head -n 1 || true)
[ -n "$current" ] || fail "In der .env fehlt eine Zeile MAKERHUB_IMAGE=ghcr.io/OWNER/REPO:latest. Nichts geändert."
image=${current#MAKERHUB_IMAGE=}
case $image in
  *:*) old_tag=${image##*:}; image=${image%:*} ;;
  *) fail "Die Zeile MAKERHUB_IMAGE in der .env hat keinen Tag (…:latest). Nichts geändert." ;;
esac
if ! printf '%s' "$image" | grep -Eq '^ghcr\.io/[a-z0-9._-]+/[a-z0-9._-]+$'; then
  fail "MAKERHUB_IMAGE in der .env sieht nicht nach einem ghcr.io-Image aus. Nichts geändert."
fi

write_tag() {
  # Rewrites the file in place (cat >), so its permissions stay as they are.
  new=$(awk -v line="MAKERHUB_IMAGE=$image:$1" '
    /^MAKERHUB_IMAGE=/ && !done { print line; done = 1; next } { print }' .env)
  printf '%s\n' "$new" > .env
}

echo "Wechsel: $old_tag -> $tag"
# Pull first with the new tag given directly (it wins over .env); .env is only changed
# when this worked, so an abort (Ctrl-C, lost connection) leaves it as it was.
if ! MAKERHUB_IMAGE="$image:$tag" docker compose pull; then
  fail "Das Image $image:$tag konnte nicht geholt werden (gibt es den Pull Request, ist der Build fertig?). Die laufende Version bleibt, nichts geändert."
fi
write_tag "$tag"
if ! docker compose up -d --no-build; then
  write_tag "$old_tag"
  fail "Der Neustart ist fehlgeschlagen. Die .env zeigt wieder auf $old_tag."
fi
docker compose ps
echo "Läuft jetzt: $tag"
