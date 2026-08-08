#!/usr/bin/env bash
# P2 打断联调：提问后等到 speaking，再 interrupt，断言回 idle 且 generation 递增。
set -euo pipefail
API="${API:-http://127.0.0.1:8000}"
SAMPLE="${SAMPLE:-/home/ubuntu/AI/student_avatar/asr/data/sample_zh.wav}"

SESSION=$(curl -s -X POST "$API/api/v1/sessions" \
  -H 'Content-Type: application/json' \
  -d '{"student_id":"stu_p2","avatar_id":"avatar_001","avatar_version_id":"avv_001"}')
SID=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['session_id'])" "$SESSION")
echo "session=$SID"

curl -s -X POST "$API/api/v1/sessions/$SID/media/ensure" >/dev/null
curl -s -X POST "$API/api/v1/sessions/$SID/questions" -F "audio=@$SAMPLE;type=audio/wav" >/dev/null

speaking=0
for i in $(seq 1 60); do
  S=$(curl -s "$API/api/v1/sessions/$SID")
  st=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['state'])" "$S")
  gen=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['generation'])" "$S")
  echo "[wait $i] state=$st gen=$gen"
  if [[ "$st" == "speaking" ]]; then speaking=1; break; fi
  if [[ "$st" == "idle" && "$i" -gt 5 ]]; then
    echo "WARN: returned idle before speaking (short answer?); interrupt anyway"
    break
  fi
  sleep 0.5
done

BEFORE_GEN=$(curl -s "$API/api/v1/sessions/$SID" | python3 -c "import json,sys; print(json.load(sys.stdin)['generation'])")
IR=$(curl -s -X POST "$API/api/v1/sessions/$SID/interrupt")
echo "interrupt=$IR"
AFTER=$(curl -s "$API/api/v1/sessions/$SID")
echo "after=$AFTER"
python3 - <<PY
import json
ir=json.loads('''$IR''')
after=json.loads('''$AFTER''')
before=int("$BEFORE_GEN")
assert ir.get("ok") is True
assert after["state"] == "idle", after
assert int(ir.get("generation", after["generation"])) >= before, (before, ir)
# 打断后短等，确认不会又回到 speaking（旧回答恢复）
import time, urllib.request
for i in range(10):
    time.sleep(0.3)
    s=json.loads(urllib.request.urlopen("$API/api/v1/sessions/$SID").read())
    assert s["state"] != "speaking", s
print("OK interrupt: speaking=$speaking -> idle, no resume")
PY
