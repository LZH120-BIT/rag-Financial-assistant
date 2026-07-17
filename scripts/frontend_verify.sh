#!/usr/bin/env bash
# 前端联调多方位验证 - 通过后端 HTTP 接口模拟前端行为
# 覆盖场景：认证 / 检索 / 拒答 / 中英文 / section / 跨公司 / 多轮上下文 / 边界
#
# 修复历史：
# - v2: shell 变量与 Python 代码解耦（改用环境变量传数据），避免中文注入 Python
#       字符串引发 "Non-UTF-8" 报错；body 构造改走 stdin JSON，避免单引号插值。
set -u
export PYTHONIOENCODING=utf-8
export LC_ALL=en_US.UTF-8
export LANG=en_US.UTF-8

BASE="http://localhost:7000"
OUT="/tmp/rag-logs/curl_verify.log"
SID_TMP="/tmp/rag-logs/last_sid.txt"
mkdir -p "$(dirname "$OUT")"
: > "$OUT"

log() { echo -e "\n\n============================================================" | tee -a "$OUT"
        echo "$1" | tee -a "$OUT"
        echo "============================================================" | tee -a "$OUT"; }

# ---------- 0. 登录拿 token ----------
log "[0] 登录拿 token"
TOK=$(curl -s -X POST "$BASE/userinfo/loginuser" \
  -H "Content-Type: application/json" \
  -d '{"phoneNumber":"13800138000","password":"test123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['token'])")
echo "token: ${TOK:0:40}..." | tee -a "$OUT"

# ---------- 辅助函数 ----------
# 通用原则：Python 代码里绝不出现由 shell 插值来的用户数据，避免任何字符集/引号问题。
# 数据通过环境变量传给 Python；Python 侧用 os.environ 读，字节->utf8 由 PYTHONIOENCODING 保证。
ask() {
  local label="$1" ; local content="$2" ; local session="${3:-null}"
  log "[$label] Q: $content"

  # 1. 构造 body：Python 从 env 读 content/session，输出 JSON
  local body
  body=$(CONTENT="$content" SESSION="$session" python3 -c "
import os, json, sys
sys.stdout.reconfigure(encoding='utf-8')
print(json.dumps({
    'content': os.environ['CONTENT'],
    'sessionId': os.environ['SESSION'],
    'uploadFileList': [],
    'isKnowledgeBased': True,
}, ensure_ascii=False))
")

  # 2. 发请求，raw response 写到临时文件（避免走 shell 变量再次经历字符解析）
  local raw_tmp; raw_tmp=$(mktemp)
  curl -sN -X POST "$BASE/chat/sendmessage" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOK" \
    -d "$body" --max-time 90 > "$raw_tmp"

  # 3. 解析：Python 从文件 stdin 读，全程 utf-8，输出到 tee
  RAW_FILE="$raw_tmp" SID_OUT="$SID_TMP" python3 -c "
import os, json, sys
sys.stdout.reconfigure(encoding='utf-8')
raw = open(os.environ['RAW_FILE'], 'r', encoding='utf-8').read()
events = [e for e in raw.split('###ABC###') if e.strip()]
docs, answer, sid = [], '', ''
for e in events:
    try: obj = json.loads(e)
    except Exception: continue
    if obj.get('type') == 'queryKB' and obj.get('statusInfo') == 'completed':
        docs = obj.get('fileList', [])
    elif obj.get('role') == 'assistant':
        answer += obj.get('content', '')
    elif obj.get('role') == 'sessionId':
        sid = obj.get('content', '')
print(f'  → 命中 {len(docs)} 篇:')
for d in docs[:6]:
    print(f'      - {d}')
if len(docs) > 6:
    print(f'      ... +{len(docs)-6} more')
suffix = '...' if len(answer) > 400 else ''
print(f'  → LLM 答案: {answer[:400]}{suffix}')
print(f'  → sessionId: {sid}')
# 顺手把 sid 写到临时文件供下一轮追问使用
if sid:
    open(os.environ['SID_OUT'], 'w', encoding='utf-8').write(sid)
" | tee -a "$OUT"

  rm -f "$raw_tmp"
}

# ==================== 场景 1: 精确数字 ====================
ask "1-EN-数字" "What was Microsoft total revenue and net income in fiscal year 2022?"

# ==================== 场景 2: 中文问英文财报 ====================
ask "2-中文-数字" "微软 2022 财年的研发费用是多少？"

# ==================== 场景 3: 跨公司列表 ====================
ask "3-枚举-biotech" "List the top biotechnology companies mentioned in the knowledge base."

# ==================== 场景 4: 定性描述 ====================
ask "4-定性-ESG" "What is Microsoft approach to sustainability and carbon emissions?"

# ==================== 场景 5: 拒答 - 非金融 ====================
ask "5-拒答-闲聊" "推荐一部好看的电影"

# ==================== 场景 6: 拒答 - 投资建议 ====================
ask "6-拒答-投资" "Should I buy Microsoft stock right now?"

# ==================== 场景 7: 冷门公司 ====================
ask "7-冷门-Weis" "Weis Markets 是做什么业务的？"

# ==================== 场景 8: 多轮上下文（关联问题）====================
ask "8a-多轮-第1轮" "What was Toshiba fiscal year 2021 net sales?"
SID_MULTI=$(cat "$SID_TMP" 2>/dev/null || echo "null")
echo "[NOTE] 保留 sessionId=$SID_MULTI 用于第2轮" | tee -a "$OUT"
ask "8b-多轮-第2轮追问" "那和上一年比增长多少？" "$SID_MULTI"

# ==================== 场景 9: 错误 token ====================
log "[9] 错误 token 验证认证"
resp=$(curl -s -X POST "$BASE/chat/sendmessage" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer INVALID_TOKEN" \
  -d '{"content":"hi","sessionId":"null","uploadFileList":[],"isKnowledgeBased":true}')
echo "response: $resp" | tee -a "$OUT"

# ==================== 场景 10: 空问题边界 ====================
ask "10-边界-空问题" "?"

echo -e "\n\n============ ALL DONE ============" | tee -a "$OUT"
echo "Full log: $OUT"
