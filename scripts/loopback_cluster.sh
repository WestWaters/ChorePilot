#!/bin/bash
# Two-node llama.cpp RPC cluster on ONE machine: a "dishwasher" worker (1 GB profile, CPU only)
# plus this machine as host. Proves discovery, the split plan, and --device/--tensor-split mapping.
# usage: DISHSOAKER_LLAMA_DIR=/path/to/rpc/build/bin scripts/loopback_cluster.sh model.gguf
set -u
M=${1:?model.gguf}
cd "$(dirname "$0")/.."
python3 -m dishsoaker serve --worker --profile dishwasher-1g --cpu-only > /tmp/dishsoaker-worker.log 2>&1 &
W=$!
trap 'kill $H 2>/dev/null; sleep 1; kill $W 2>/dev/null' EXIT   # host first, then worker
sleep 3
python3 -m dishsoaker scan 127.0.0.1
python3 -m dishsoaker serve --model "$M" --rpc 127.0.0.1:50052 --strategy proportional --ctx 2048 --port 18081 &
H=$!
for i in $(seq 1 120); do curl -sf http://127.0.0.1:18081/health >/dev/null && break; sleep 1; done
curl -s http://127.0.0.1:18081/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"What does a dishwasher do? One sentence."}],"max_tokens":40}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['choices'][0]['message']['content']); print('gen tok/s', d.get('timings',{}).get('predicted_per_second'))"
