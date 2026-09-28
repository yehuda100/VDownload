#!/usr/bin/env bash
# Fast-forward /var/www/yehuda100/bot3 to origin/V2.0 and restart screen session Bot3.
#
# Run on the server as root:
#   bash /var/www/yehuda100/bot3/scripts/deploy.sh
#
# GitHub Actions pulls first so this file is the copy from origin/V2.0, then
# calls: deploy.sh --baseline <commit-before-that-pull>
# The baseline keeps requirements.txt detection working after that outer pull.
#
# Stops before changing the checkout when tracked files are dirty or when
# origin/V2.0 cannot fast-forward. Does not discard local work, does not edit
# config.py, and does not replace the bot3/ virtualenv (pip install only
# writes into it when requirements.txt changed).

set -euo pipefail

REPO_DIR="/var/www/yehuda100/bot3"
BRANCH="V2.0"
START_CMD="cd /var/www/yehuda100/bot3 && source bot3/bin/activate && python3 main.py"
STAMP_FILE="${REPO_DIR}/.requirements-installed-blob"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: deploy.sh [--baseline <commit>]

Fast-forward /var/www/yehuda100/bot3 to origin/V2.0 and restart the Bot3
screen session. --baseline is the commit that was checked out before a
wrapper pulled, so requirements.txt changes are still detected.
EOF
}

git_blob() {
  local spec=$1
  local blob
  if blob=$(git rev-parse --verify --quiet "$spec"); then
    printf '%s\n' "$blob"
  fi
  return 0
}

redact_stream() {
  # Telegram bot tokens look like 123456789:AAH... (id of 6+ digits, secret of 30+).
  sed -E 's/[0-9]{6,}:[A-Za-z0-9_-]{30,}/<REDACTED>/g'
}

assert_deployable_worktree() {
  local branch dirty
  branch=$(git rev-parse --abbrev-ref HEAD)
  if [[ "$branch" != "$BRANCH" ]]; then
    die "checkout is on ${branch}, expected ${BRANCH}. The worktree was left as it is."
  fi
  dirty=$(git status --porcelain --untracked-files=no)
  if [[ -n "$dirty" ]]; then
    echo "ERROR: tracked files have local changes, so the deploy stopped before updating the checkout." >&2
    echo "config.py and the bot3/ virtualenv were left as they are." >&2
    printf '%s\n' "$dirty" >&2
    exit 1
  fi
}

screen_session_ready() {
  local listing bot_lines
  listing=$(screen -ls 2>&1 || true)
  bot_lines=$(grep -E '[0-9]+\.Bot3[[:space:]]' <<<"$listing" || true)
  if [[ -z "$bot_lines" ]]; then
    return 1
  fi
  if grep -vi 'dead' <<<"$bot_lines" >/dev/null; then
    return 0
  fi
  echo "Removing dead screen session Bot3."
  screen -wipe >/dev/null 2>&1 || true
  return 1
}

# PIDs of python interpreters running main.py with cwd REPO_DIR.
bot_pids() {
  local proc pid comm cmdline cwd state
  for proc in /proc/[0-9]*; do
    [[ -d "$proc" ]] || continue
    pid=${proc#/proc/}
    comm=$(cat "$proc/comm" 2>/dev/null || true)
    comm=${comm//$'\n'/}
    # comm is the executable basename ("python3", sometimes "python3.12").
    [[ "$comm" == python || "$comm" == python3 || "$comm" == python3.* ]] || continue
    # A process can exit between listing /proc and reading it. Keep scanning.
    state=$(sed -n 's/.*) //p' "$proc/stat" 2>/dev/null | awk '{print $1}' || true)
    [[ "$state" == "Z" ]] && continue
    cwd=$(readlink -f "$proc/cwd" 2>/dev/null || true)
    [[ "$cwd" == "$REPO_DIR" ]] || continue
    cmdline=$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null || true)
    [[ "$cmdline" =~ (^|[[:space:]/])python([0-9.]*)?[[:space:]]+main\.py([[:space:]]|$) ]] || continue
    printf '%s\n' "$pid"
  done
  return 0
}

wait_for_exit() {
  local seconds=$1
  local elapsed=0
  local pids
  while true; do
    pids=$(bot_pids)
    if [[ -z "$pids" ]]; then
      return 0
    fi
    if (( elapsed >= seconds )); then
      return 1
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
}

signal_matching_bots() {
  local sig=$1
  local pid
  local found=0
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    found=1
    echo "Sending SIG${sig} to ${pid}"
    if command -v pkill >/dev/null 2>&1; then
      pkill -s "$sig" -P "$pid" 2>/dev/null || true
    fi
    if ! kill -s "$sig" "$pid" 2>/dev/null; then
      echo "Process ${pid} was already gone."
    fi
  done < <(bot_pids)
  (( found == 1 ))
}

restart_in_existing_session() {
  echo "Sending Ctrl-C to screen session Bot3."
  screen -S Bot3 -p 0 -X stuff $'\003'
  if ! wait_for_exit 30; then
    echo "Bot still running after Ctrl-C; sending SIGTERM."
    signal_matching_bots TERM || true
    if ! wait_for_exit 10; then
      echo "Bot still running after SIGTERM; sending SIGKILL."
      signal_matching_bots KILL || true
      if ! wait_for_exit 5; then
        die "python3 main.py (cwd ${REPO_DIR}) did not exit; the new process was not started"
      fi
    fi
  fi
  # Let the screen shell return to a prompt before typing the start command.
  sleep 1
  echo "Starting bot in screen session Bot3."
  screen -S Bot3 -p 0 -X stuff "${START_CMD}"$'\n'
}

start_new_session() {
  echo "Screen session Bot3 does not exist; creating it."
  screen -dmS Bot3 bash -c 'cd /var/www/yehuda100/bot3 && source bot3/bin/activate && python3 main.py; exec bash'
}

dump_screen() {
  local dump
  dump=$(mktemp)
  chmod 600 "$dump"
  if screen -S Bot3 -p 0 -X hardcopy "$dump"; then
    sleep 1
    echo "----- screen Bot3 hardcopy (tokens redacted) -----"
    if [[ -s "$dump" ]]; then
      tail -n 80 "$dump" | redact_stream || true
    else
      echo "(screen hardcopy was empty)"
    fi
    echo "----- end screen hardcopy -----"
  else
    echo "Could not capture a screen hardcopy from Bot3."
  fi
  rm -f "$dump"
}

health_check() {
  local pids
  echo "Waiting 15s before checking that the bot is still running."
  sleep 15
  pids=$(bot_pids)
  if [[ -z "$pids" ]]; then
    echo "ERROR: python3 main.py is not running with cwd ${REPO_DIR}." >&2
    dump_screen
    exit 1
  fi
  echo "Bot is running from ${REPO_DIR} (pid ${pids//$'\n'/ })."
}

sync_requirements() {
  local old_head=$1
  local new_head=$2
  local installed_blob=""
  local current_blob=""

  if [[ -f "$STAMP_FILE" ]]; then
    installed_blob=$(tr -d '[:space:]' < "$STAMP_FILE")
  else
    # First automated deploy: the virtualenv already matches the pre-pull tree.
    installed_blob=$(git_blob "${old_head}:requirements.txt")
    if [[ -n "$installed_blob" ]]; then
      printf '%s\n' "$installed_blob" > "$STAMP_FILE"
    fi
  fi

  current_blob=$(git_blob "${new_head}:requirements.txt")
  if [[ -z "$current_blob" || ! -f "${REPO_DIR}/requirements.txt" ]]; then
    die "requirements.txt is missing at ${new_head}"
  fi

  if [[ "$current_blob" == "$installed_blob" ]]; then
    echo "requirements.txt matches the installed snapshot; skipping pip install."
    return 0
  fi

  if [[ ! -x "${REPO_DIR}/bot3/bin/pip" ]]; then
    die "requirements.txt changed but ${REPO_DIR}/bot3/bin/pip is not executable. The virtualenv was not recreated."
  fi

  echo "requirements.txt changed between ${old_head} and ${new_head}; installing with bot3/bin/pip."
  "${REPO_DIR}/bot3/bin/pip" install -r "${REPO_DIR}/requirements.txt"
  printf '%s\n' "$current_blob" > "$STAMP_FILE"
}

main() {
  local baseline=""
  local old_head
  local new_head

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --baseline)
        [[ $# -ge 2 ]] || die "--baseline requires a commit id"
        baseline=$2
        shift 2
        ;;
      -h | --help)
        usage
        exit 0
        ;;
      *)
        die "unknown argument: $1"
        ;;
    esac
  done

  export GIT_TERMINAL_PROMPT=0
  export TERM="${TERM:-xterm}"
  cd "$REPO_DIR" || die "cannot cd to ${REPO_DIR}"
  [[ -d /proc ]] || die "Linux /proc is required to identify the bot process"
  command -v git >/dev/null 2>&1 || die "git is not installed"
  command -v screen >/dev/null 2>&1 || die "screen is not installed"

  if [[ -n "$baseline" ]]; then
    old_head=$(git rev-parse --verify "${baseline}^{commit}") || die "baseline ${baseline} is not a commit"
  else
    old_head=$(git rev-parse HEAD)
  fi

  assert_deployable_worktree

  echo "Fetching origin ${BRANCH}."
  if ! git fetch origin "$BRANCH"; then
    die "git fetch origin ${BRANCH} failed. The checkout was left as it is."
  fi
  echo "Fast-forwarding to origin/${BRANCH}."
  if ! git pull --ff-only origin "$BRANCH"; then
    die "fast-forward pull of origin/${BRANCH} failed. The checkout was left as it is."
  fi

  new_head=$(git rev-parse HEAD)
  if [[ -n "$baseline" ]] && ! git merge-base --is-ancestor "$old_head" "$new_head"; then
    die "baseline ${old_head} is not an ancestor of ${new_head}. The checkout was left as it is."
  fi

  echo "Old commit: ${old_head}"
  echo "New commit: ${new_head}"

  sync_requirements "$old_head" "$new_head"

  if [[ ! -f "${REPO_DIR}/bot3/bin/activate" ]]; then
    die "missing ${REPO_DIR}/bot3/bin/activate. The bot3/ virtualenv was not modified."
  fi

  if screen_session_ready; then
    restart_in_existing_session
  else
    start_new_session
  fi

  health_check
  echo "Old commit: ${old_head}"
  echo "New commit: ${new_head}"
  echo "Deploy finished."
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
