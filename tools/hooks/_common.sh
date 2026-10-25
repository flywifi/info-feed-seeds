# Shared by the info-feed-seeds hooks. Sourced, not run.
# Install the hooks once per clone:  git config core.hooksPath tools/hooks
# The hooks are opt-in: a clone without that setting runs none of them, and
# git commit --no-verify or git push --no-verify skips them without a word.

skip_if_bypassed() {
  if [ "${IFS_SKIP_HOOKS:-}" = "1" ]; then
    {
      echo "##################################################################"
      echo "#  WARNING: IFS_SKIP_HOOKS=1 is set. The $1 hook did NOT run."
      echo "#  Nothing was checked. CI runs the same checks on the pull"
      echo "#  request, and the main ruleset requires them, so a red run"
      echo "#  blocks the merge there instead of here."
      echo "##################################################################"
    } >&2
    exit 0
  fi
}

find_python() {
  if [ "${OS:-}" = "Windows_NT" ] && command -v py >/dev/null 2>&1; then
    PY="py -3"
  elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
  elif command -v py >/dev/null 2>&1; then
    PY="py -3"
  else
    echo "info-feed-seeds hook: no python3 or py launcher found" >&2
    exit 1
  fi
  ROOT=$(git rev-parse --show-toplevel) || exit 1
}
