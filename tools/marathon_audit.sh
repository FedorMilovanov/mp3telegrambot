#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TOTAL=0
PASS=0
WARN=0
FAIL=0

record() {
  local severity="$1" name="$2" rc="$3" detail="${4:-}"
  TOTAL=$((TOTAL + 1))
  if [[ "$rc" -eq 0 ]]; then
    PASS=$((PASS + 1))
    printf 'CHECK|PASS|%s|%s|\n' "$severity" "$name"
  elif [[ "$severity" == "hard" ]]; then
    FAIL=$((FAIL + 1))
    printf 'CHECK|FAIL|%s|%s|%s\n' "$severity" "$name" "$detail"
  else
    WARN=$((WARN + 1))
    printf 'CHECK|WARN|%s|%s|%s\n' "$severity" "$name" "$detail"
  fi
}

run_check() {
  local severity="$1" name="$2"
  shift 2
  local output rc
  output="$({ "$@"; } 2>&1)"
  rc=$?
  record "$severity" "$name" "$rc" "$(printf '%s' "$output" | tail -c 700 | tr '\n' ' ')"
}

no_matches() {
  local pattern="$1"
  shift
  if git grep -nE "$pattern" -- "$@" > /tmp/marathon-grep.txt 2>&1; then
    cat /tmp/marathon-grep.txt
    return 1
  fi
  return 0
}

require_match() {
  local pattern="$1"
  shift
  git grep -nE "$pattern" -- "$@" >/dev/null
}

check_text_utf8() {
  python - <<'PY'
from pathlib import Path
bad=[]
for p in Path('.').rglob('*'):
    if '.git' in p.parts or not p.is_file() or p.suffix.lower() not in {'.py','.md','.json','.toml','.yml','.yaml','.txt'}:
        continue
    try: p.read_text(encoding='utf-8')
    except Exception as exc: bad.append(f'{p}: {exc}')
if bad:
    raise SystemExit('\n'.join(bad))
PY
}

check_no_nul() {
  python - <<'PY'
from pathlib import Path
bad=[]
for p in Path('.').rglob('*'):
    if '.git' in p.parts or not p.is_file() or p.suffix.lower() not in {'.py','.md','.json','.toml','.yml','.yaml','.txt'}:
        continue
    if b'\0' in p.read_bytes(): bad.append(str(p))
if bad: raise SystemExit('NUL bytes: '+', '.join(bad))
PY
}

check_json() {
  python - <<'PY'
import json
from pathlib import Path
bad=[]
for p in Path('.').rglob('*.json'):
    if '.git' in p.parts: continue
    try: json.loads(p.read_text(encoding='utf-8'))
    except Exception as exc: bad.append(f'{p}: {exc}')
if bad: raise SystemExit('\n'.join(bad))
PY
}

check_yaml() {
  python - <<'PY'
from pathlib import Path
import yaml
bad=[]
for pattern in ('*.yml','*.yaml'):
    for p in Path('.').rglob(pattern):
        if '.git' in p.parts: continue
        try: yaml.safe_load(p.read_text(encoding='utf-8'))
        except Exception as exc: bad.append(f'{p}: {exc}')
if bad: raise SystemExit('\n'.join(bad))
PY
}

check_ast_blocking_in_async() {
  python - <<'PY'
import ast
from pathlib import Path
bad=[]
for base in ('core','services','pipelines','converters','handlers'):
    root=Path(base)
    if not root.exists(): continue
    for p in root.rglob('*.py'):
        try: tree=ast.parse(p.read_text(encoding='utf-8'))
        except Exception: continue
        for node in ast.walk(tree):
            if not isinstance(node,(ast.AsyncFunctionDef,)): continue
            for sub in ast.walk(node):
                if isinstance(sub,ast.Call) and isinstance(sub.func,ast.Attribute):
                    owner=getattr(sub.func.value,'id',None)
                    if (owner,sub.func.attr) in {('time','sleep'),('requests','get'),('requests','post'),('subprocess','run')}:
                        bad.append(f'{p}:{sub.lineno}:{node.name}:{owner}.{sub.func.attr}')
if bad: raise SystemExit('\n'.join(bad[:40]))
PY
}

check_subprocess_timeouts() {
  python - <<'PY'
import ast
from pathlib import Path
bad=[]
for base in ('core','services','pipelines','converters','handlers'):
    root=Path(base)
    if not root.exists(): continue
    for p in root.rglob('*.py'):
        try: tree=ast.parse(p.read_text(encoding='utf-8'))
        except Exception: continue
        for node in ast.walk(tree):
            if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Attribute): continue
            if getattr(node.func.value,'id',None)=='subprocess' and node.func.attr in {'run','check_output','check_call'}:
                if not any(k.arg=='timeout' for k in node.keywords):
                    bad.append(f'{p}:{node.lineno}:subprocess.{node.func.attr}')
if bad: raise SystemExit('\n'.join(bad[:60]))
PY
}

check_requests_timeouts() {
  python - <<'PY'
import ast
from pathlib import Path
bad=[]
for base in ('core','services','pipelines','converters','handlers'):
    root=Path(base)
    if not root.exists(): continue
    for p in root.rglob('*.py'):
        try: tree=ast.parse(p.read_text(encoding='utf-8'))
        except Exception: continue
        for node in ast.walk(tree):
            if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Attribute): continue
            if getattr(node.func.value,'id',None)=='requests' and node.func.attr in {'get','post','put','patch','delete'}:
                if not any(k.arg=='timeout' for k in node.keywords):
                    bad.append(f'{p}:{node.lineno}:requests.{node.func.attr}')
if bad: raise SystemExit('\n'.join(bad[:60]))
PY
}

check_bare_except() {
  python - <<'PY'
import ast
from pathlib import Path
bad=[]
for base in ('core','services','pipelines','converters','handlers'):
    root=Path(base)
    if not root.exists(): continue
    for p in root.rglob('*.py'):
        try: tree=ast.parse(p.read_text(encoding='utf-8'))
        except Exception: continue
        for node in ast.walk(tree):
            if isinstance(node,ast.ExceptHandler) and node.type is None:
                bad.append(f'{p}:{node.lineno}')
if bad: raise SystemExit('\n'.join(bad[:60]))
PY
}

check_duplicate_requirements() {
  python - <<'PY'
from pathlib import Path
from collections import Counter
bad=[]
for p in Path('.').glob('requirements*.txt'):
    names=[]
    for raw in p.read_text(encoding='utf-8').splitlines():
        line=raw.strip()
        if not line or line.startswith(('#','-')): continue
        name=line.split(';',1)[0]
        for token in ('==','>=','<=','~=','!=','>','<','['): name=name.split(token,1)[0]
        names.append(name.casefold().replace('_','-').strip())
    dup=[name for name,count in Counter(names).items() if count>1]
    if dup: bad.append(f'{p}: {dup}')
if bad: raise SystemExit('\n'.join(bad))
PY
}

run_check hard python_compileall python -m compileall -q .
run_check hard pytest_collect python -m pytest --collect-only -q
run_check hard ruff_repository python -m ruff check .
run_check hard git_diff_check git diff --check main...HEAD
run_check hard no_merge_conflict_markers no_matches '^(<<<<<<<|=======|>>>>>>>)' '*.py' '*.yml' '*.yaml' '*.json' '*.md'
run_check hard no_tracked_dotenv bash -lc "! git ls-files | grep -E '(^|/)\.env($|\.)' | grep -vE '(^|/)\.env\.example$'"
run_check hard no_tracked_pyc bash -lc "! git ls-files | grep -E '\.(pyc|pyo)$'"
run_check hard no_tracked_pycache bash -lc "! git ls-files | grep -E '(^|/)__pycache__/|(^|/)\.pytest_cache/'"
run_check hard no_backup_temp_files bash -lc "! git ls-files | grep -Ei '(~$|\.bak$|\.orig$|\.rej$|\.tmp$)'"
run_check hard no_one_time_workflows bash -lc "! git ls-files '.github/workflows/*' | grep -Ei 'one[-_ ]?time|temporary|temp[-_ ]?audit'"
run_check hard utf8_text_files check_text_utf8
run_check hard no_nul_text_files check_no_nul
run_check hard json_documents_parse check_json
run_check hard yaml_documents_parse check_yaml
run_check advisory duplicate_requirements check_duplicate_requirements
run_check hard no_pull_request_target no_matches 'pull_request_target' '.github/workflows/*.yml' '.github/workflows/*.yaml'
run_check hard no_write_all_permissions no_matches 'permissions:[[:space:]]*write-all' '.github/workflows/*.yml' '.github/workflows/*.yaml'
run_check advisory no_shell_true no_matches 'shell[[:space:]]*=[[:space:]]*True' 'core/*.py' 'services/*.py' 'pipelines/*.py' 'converters/*.py' 'handlers/*.py'
run_check advisory no_os_system no_matches 'os\.system\(' 'core/*.py' 'services/*.py' 'pipelines/*.py' 'converters/*.py' 'handlers/*.py'
run_check advisory no_eval_exec no_matches '(^|[^A-Za-z_])(eval|exec)\(' 'core/*.py' 'services/*.py' 'pipelines/*.py' 'converters/*.py' 'handlers/*.py'
run_check hard no_insecure_mktemp no_matches 'tempfile\.mktemp\(' 'core/*.py' 'services/*.py' 'pipelines/*.py' 'converters/*.py' 'handlers/*.py'
run_check advisory no_pickle_loads no_matches 'pickle\.loads?\(' 'core/*.py' 'services/*.py' 'pipelines/*.py' 'converters/*.py' 'handlers/*.py'
run_check advisory no_unsafe_yaml_load no_matches 'yaml\.load\(' 'core/*.py' 'services/*.py' 'pipelines/*.py' 'converters/*.py' 'handlers/*.py'
run_check advisory no_blocking_calls_in_async check_ast_blocking_in_async
run_check advisory subprocess_calls_have_timeout check_subprocess_timeouts
run_check advisory requests_calls_have_timeout check_requests_timeouts
run_check advisory no_bare_except check_bare_except
run_check hard local_bot_api_documented_required require_match 'Local Bot API.*(обязател|обязатель)' '.env.example'
run_check hard cloud_media_fallback_disabled require_match 'CLOUD_FALLBACK=0|cloud.*fallback.*выключ' '.env.example'
run_check hard highlights_final_delivery_gate require_match 'verify_highlights_delivery' 'pipelines/montage.py'
run_check hard highlights_final_size_checked require_match 'финальный файл.*после всех этапов|final_size' 'pipelines/montage.py'
run_check hard highlights_audio_48khz require_match 'aresample=48000|-ar.*48000' 'services/highlights_quality.py'
run_check hard highlights_no_time_only_merge bash -lc "! grep -F 'refined = _merge_adjacent_fragments(refined)' services/highlights_quality.py"
run_check hard clips_wall_clock_budget require_match 'CLIPS_CANDIDATE_BUDGET_SECONDS' 'pipelines/clips.py'
run_check hard telegraph_content_too_big_classified require_match 'CONTENT_TOO_BIG' 'services/telegraph_edit.py'
run_check hard shorts_final_size_checked require_match 'финальный файл.*после всех|final_size' 'pipelines/shorts.py'
run_check hard shorts_measured_delivery_duration require_match 'measured_final_duration|delivery_duration' 'pipelines/shorts.py'
run_check hard caption_safe_trim require_match 'safe_trim_caption' 'pipelines/shorts.py' 'pipelines/montage.py'
run_check hard startup_no_none_none bash -lc "! git grep -nF 'None; None' -- '*.py'"
run_check advisory no_secret_names_in_logs no_matches 'logger\.(debug|info|warning|error).*?(BOT_TOKEN|GEMINI_API_KEY|API_HASH|ACCESS_TOKEN)' 'core/*.py' 'services/*.py' 'pipelines/*.py' 'handlers/*.py'
run_check advisory no_hardcoded_secret_shapes no_matches '(AIza[0-9A-Za-z_-]{30,}|[0-9]{8,10}:[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,})' '*.py' '*.yml' '*.yaml' '*.json' '*.md'

probe_output="$(python tools/marathon_probes.py 2>&1)"
probe_rc=$?
printf '%s\n' "$probe_output"
probe_total="$(printf '%s\n' "$probe_output" | grep -c '^PROBE|' || true)"
probe_pass="$(printf '%s\n' "$probe_output" | grep -c '^PROBE|PASS|' || true)"
probe_warn="$(printf '%s\n' "$probe_output" | grep -c '^PROBE|WARN|' || true)"
probe_fail="$(printf '%s\n' "$probe_output" | grep -c '^PROBE|FAIL|' || true)"
TOTAL=$((TOTAL + probe_total))
PASS=$((PASS + probe_pass))
WARN=$((WARN + probe_warn))
FAIL=$((FAIL + probe_fail))
if [[ "$probe_rc" -ne 0 && "$probe_fail" -eq 0 ]]; then
  FAIL=$((FAIL + 1)); TOTAL=$((TOTAL + 1))
  printf 'CHECK|FAIL|hard|probe_runner_exit|rc=%s\n' "$probe_rc"
fi

printf 'MARATHON_SUMMARY|total=%d|pass=%d|warn=%d|fail=%d\n' "$TOTAL" "$PASS" "$WARN" "$FAIL"
printf 'MARATHON_RECON_SENTINEL|The first run intentionally exits 86 so pytest exposes the complete captured report.\n'
exit 86
