#!/bin/zsh
# Phase 8 冒烟测试: 12 个 API 可达性 + 默认开关行为 (临时脚本, 测试后删除)
TOKEN=$(cat /tmp/p8_token.txt)
H="Authorization: Bearer $TOKEN"
B="http://localhost:8000/api/v1"
CID=4
PASS=0; FAIL=0

chk() { # chk <名称> <期望HTTP码> <期望code字段> <实际HTTP码> <响应体>
  local name=$1 want_http=$2 want_code=$3 http=$4 body=$5
  local code=$(echo "$body" | python3 -c "import json,sys;print(json.load(sys.stdin).get('code',''))" 2>/dev/null)
  if [ "$http" = "$want_http" ] && { [ -z "$want_code" ] || [ "$code" = "$want_code" ]; }; then
    echo "PASS  $name (http=$http code=$code)"; PASS=$((PASS+1))
  else
    echo "FAIL  $name (http=$http code=$code want=$want_http/$want_code) body=$(echo $body|head -c 200)"; FAIL=$((FAIL+1))
  fi
}

r=$(curl -s -w "\n%{http_code}" "$B/ai-ops/stats/" -H $H); chk "GET ai-ops/stats" 200 OK "${r##*$'\n'}" "${r%$'\n'*}"
r=$(curl -s -w "\n%{http_code}" "$B/ai-ops/llm-calls/" -H $H); chk "GET ai-ops/llm-calls" 200 OK "${r##*$'\n'}" "${r%$'\n'*}"
r=$(curl -s -w "\n%{http_code}" "$B/ai-ops/rule-stats/" -H $H); chk "GET ai-ops/rule-stats" 200 OK "${r##*$'\n'}" "${r%$'\n'*}"
r=$(curl -s -w "\n%{http_code}" "$B/databases/$CID/changes/" -H $H); chk "GET databases/$CID/changes" 200 OK "${r##*$'\n'}" "${r%$'\n'*}"
r=$(curl -s -w "\n%{http_code}" "$B/databases/$CID/autonomy/" -H $H); chk "GET autonomy" 200 OK "${r##*$'\n'}" "${r%$'\n'*}"
r=$(curl -s -w "\n%{http_code}" "$B/databases/$CID/causal-graph/" -H $H); chk "GET causal-graph" 200 OK "${r##*$'\n'}" "${r%$'\n'*}"

# 人工登记变更 + 幂等重复 (端点是 POST /changes/, config_id 在 body)
r=$(curl -s -w "\n%{http_code}" -X POST "$B/changes/" -H $H -H "Content-Type: application/json" -d '{"config_id":'$CID',"title":"冒烟-调整参数","change_type":"param_change"}'); chk "POST changes 登记" 200 OK "${r##*$'\n'}" "${r%$'\n'*}"
r=$(curl -s -w "\n%{http_code}" -X POST "$B/changes/" -H $H -H "Content-Type: application/json" -d '{"config_id":'$CID',"title":"冒烟-调整参数","change_type":"param_change"}'); chk "POST changes 重复(幂等)" 200 OK "${r##*$'\n'}" "${r%$'\n'*}"
r=$(curl -s -w "\n%{http_code}" -X POST "$B/changes/" -H $H -H "Content-Type: application/json" -d '{"config_id":'$CID',"title":"x","change_type":"bad_type"}'); chk "POST changes 非法类型->400" 400 BAD_REQUEST "${r##*$'\n'}" "${r%$'\n'*}"

# 自治等级设置/非法值/恢复
r=$(curl -s -w "\n%{http_code}" -X PUT "$B/databases/$CID/autonomy/" -H $H -H "Content-Type: application/json" -d '{"level":2}'); chk "PUT autonomy L2" 200 OK "${r##*$'\n'}" "${r%$'\n'*}"
r=$(curl -s -w "\n%{http_code}" -X PUT "$B/databases/$CID/autonomy/" -H $H -H "Content-Type: application/json" -d '{"level":9}'); chk "PUT autonomy 非法->8006" 400 8006 "${r##*$'\n'}" "${r%$'\n'*}"
r=$(curl -s -w "\n%{http_code}" -X PUT "$B/databases/$CID/autonomy/" -H $H -H "Content-Type: application/json" -d '{"level":null}'); chk "PUT autonomy 还原null" 200 OK "${r##*$'\n'}" "${r%$'\n'*}"

# LLM/Agent 默认关闭 → 降级响应
r=$(curl -s -w "\n%{http_code}" -X POST "$B/incidents/NON-EXIST/investigate/" -H $H); chk "POST investigate 默认关->8001" 400 8001 "${r##*$'\n'}" "${r%$'\n'*}"
r=$(curl -s -w "\n%{http_code}" -X POST "$B/incidents/NON-EXIST/rca-feedback/" -H $H -H "Content-Type: application/json" -d '{"rule_id":"R001","verdict":"correct"}'); chk "POST rca-feedback 事故不存在->8003" 404 8003 "${r##*$'\n'}" "${r%$'\n'*}"
r=$(curl -s -w "\n%{http_code}" -X POST "$B/incidents/NON-EXIST/plan-feedback/" -H $H -H "Content-Type: application/json" -d '{"scenario":"standard","verdict":"adopted"}'); chk "POST plan-feedback 事故不存在->8003" 404 8003 "${r##*$'\n'}" "${r%$'\n'*}"
r=$(curl -s -w "\n%{http_code}" "$B/incidents/NON-EXIST/agent-trace/" -H $H); chk "GET agent-trace 事故不存在->8003" 404 8003 "${r##*$'\n'}" "${r%$'\n'*}"

# LLM 连通测试 (未配置 API 地址, 应优雅报错而非 500)
r=$(curl -s -w "\n%{http_code}" -X POST "$B/llm/test-connection/" -H $H); http="${r##*$'\n'}"; body="${r%$'\n'*}"
if [ "$http" != "500" ]; then echo "PASS  POST llm/test-connection 优雅降级 (http=$http) $(echo $body|head -c 150)"; PASS=$((PASS+1)); else echo "FAIL  test-connection 500: $body"; FAIL=$((FAIL+1)); fi

# 未认证请求应被拒绝
r=$(curl -s -o /dev/null -w "%{http_code}" "$B/ai-ops/stats/")
if [ "$r" = "401" ]; then echo "PASS  未认证->401"; PASS=$((PASS+1)); else echo "FAIL  未认证 http=$r"; FAIL=$((FAIL+1)); fi

echo "=============================="
echo "冒烟结果: PASS=$PASS FAIL=$FAIL"
