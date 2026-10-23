# ChorePilot (formerly Dishsoaker3000): research notes

Can you run an LLM on a dishwasher? This file collects what the hardware actually contains, which tools exist for running big models on small boxes, and the math behind the numbers the planner uses.

Researched 2026-10-19. Every claim links to a source. **[unverified]** marks claims that rest on secondary sources, on inference or on my own estimates. **[est]** marks numbers I derived myself.

---

## 1. What is actually inside a smart appliance

### 1.1 Appliances with screens (the "big" ones)

| Appliance | SoC / CPU | RAM | Flash | OS | Source |
|---|---|---|---|---|---|
| Samsung Family Hub fridge (2019) | 1.7 GHz quad-core | 2.5 GB | 8 GB | Tizen 4.0 | [TechARP preview](https://www.techarp.com/home/2020-samsung-family-hub-preview/?amp=1) **[unverified: press-preview figures, no teardown]** |
| Samsung Bespoke 4-Door Flex, AI Family Hub+ | 1.8 GHz six-core | 3 GB | 16 GB eMMC | Tizen (same lineage as Samsung TVs) | [SamMobile](https://www.sammobile.com/samsung/bespoke-4-door-flex/) **[unverified]** |
| Samsung Bespoke AI laundry (7" "AI Home" screen) | not disclosed | not disclosed | not disclosed | not disclosed in the sources I found | [Tom's Guide](https://tomsguide.com/home/samsung-unveils-latest-ai-washing-machine-with-a-built-in-tablet-heres-our-first-look), [Samsung AU product page](https://www.samsung.com/au/washers-and-dryers/washing-machines/wf90f27-front-loading-ai-home-ai-optiwash-plus-ultra-capacity-19-5kg-gray-wf90f19adssa/) |
| LG ThinQ UP appliances | not disclosed | — | — | LG firmware; new features arrive as OTA "UP" downloads through the ThinQ app | [LG newsroom](https://www.lgnewsroom.com/2025/07/upgrade-completed-lg-thinq-up-evolves-with-you-and-because-of-you), [ThinQ UP for ACs](https://www.lg.com/global/newsroom/news/eco-solution/lg-electronics-expands-thinq-up-for-residential-air-conditioners-across-europe/) |

On paper, a Family Hub has about as much compute as a 2016 phone: 3 GB of RAM could hold a 0.5–1B Q4 model. In practice you can't use it. Tizen on these products runs only signed, store-distributed apps, and I found no documented sideload path. **[unverified: no primary source either way. Treat it as locked.]** I found no primary source saying LG runs webOS on its appliances (as opposed to its TVs). **[unverified]**

### 1.2 The Wi-Fi modules in everything else (washers, dishwashers, ovens)

Most connected appliances are a dumb mainboard plus a small Wi-Fi MCU module that speaks a serial protocol to it.

| Module | Chip | CPU | RAM | Flash | Wi-Fi | Source |
|---|---|---|---|---|---|---|
| Samsung CWAP210M | MediaTek MT7686 | Cortex-M4F @ 192 MHz | 4 MB + 384 KB | 4 MB XIP | 802.11b/g/n 1T1R, 2.4 GHz | module→chip: [HA community ESPHome project](https://community.home-assistant.io/t/convert-samsung-smartthings-devices-to-real-smart-esphome-devices-new-project/1016898); chip specs: [CNX Software](https://www.cnx-software.com/2017/06/05/mediatek-unveils-mt7686-mt7682-and-mt5932-wifi-socs-for-iot-and-smart-home-applications/) |
| Samsung CWAM210S | Samsung ARTIK (TizenRT) | ARTIK 053: Cortex-R4 @ 320 MHz | 1280 KB | 8 MB | 802.11b/g/n 2.4 GHz | [HA thread](https://community.home-assistant.io/t/convert-samsung-smartthings-devices-to-real-smart-esphome-devices-new-project/1016898), [CNX ARTIK 053](https://cnx-software.com/?p=44994) |
| MediaTek MT7682 (generic) | MT7682 | Cortex-M4F @ 192 MHz | 384 KB | 512 KB–1 MB | 802.11n 1T1R | [CNX Software](https://www.cnx-software.com/2017/06/05/mediatek-unveils-mt7686-mt7682-and-mt5932-wifi-socs-for-iot-and-smart-home-applications/) |
| Realtek RTL8710 / Ameba (generic) | RTL8710 | Cortex-M3 @ 166 MHz | 512 KB | 1 MB | 802.11n | [jpralves RTL8710 notes](https://jpralves.net/tag/rtl8710.html) |
| Espressif ESP32-S3 (generic) | ESP32-S3 | 2× Xtensa LX7 @ 240 MHz plus vector instructions | 512 KB SRAM + optional octal PSRAM | external, octal SPI | 802.11b/g/n, 2.4 GHz, 40 MHz channels | [Espressif ESP32-S3](https://www.espressif.com/en/products/socs/esp32-s3) |
| Murata SN8200 family | — | — | — | — | 802.11n; marketed for home appliances | [Electronic Specifier](https://www.electronicspecifier.com/?p=126712) |
| Bosch/Siemens Home Connect module, Miele XKM 3100 W, LG PWFMDD200 | **chip not publicly documented** | — | — | — | 2.4 GHz Wi-Fi | [Miele XKM 3100 W support](https://www.mieleusa.com/support/customer-assistance/system_integration-1332/wlan_module), [LG PWFMDD200](https://lgparts.com/products/pwfmdd200) **[unverified chipsets: no teardown found]** |

**How the module talks to the appliance.** Samsung modules use a proprietary UART protocol (read/write plus TLV datapoints) at **9600 baud** to the main PCB. The ESPHome project replaces the module with an ESP32 on the same pins ([HA thread](https://community.home-assistant.io/t/convert-samsung-smartthings-devices-to-real-smart-esphome-devices-new-project/1016898)). At 9600 baud the appliance side of the link carries about 1 KB/s. An LLM living in the module could control the machine only through that narrow pipe.

### 1.3 What is really programmable

| Layer | Programmable? | How |
|---|---|---|
| Appliance mainboard (motor, pump, heater) | No | Safety-certified firmware; reachable only through the vendor's serial protocol |
| Stock Wi-Fi module firmware | No (in practice) | Vendor OTA. Treat images as signed. **[unverified per vendor]** |
| Vendor cloud | **Yes, this is the supported path** | [Home Connect API](https://api-docs.home-connect.com/authorization/) (OAuth2 auth-code flow; `IdentifyAppliance` scope minimum; covers Bosch, Siemens, Neff, Gaggenau, Thermador and others) · [SmartThings REST API](https://developer.smartthings.com/docs/getting-started/quickstart) (`https://api.smartthings.com/v1`, personal access tokens from account.smartthings.com/tokens) · [LG ThinQ Connect](https://thinq.developer.lge.com/en/cloud/docs/thinq-connect) (REST control, status and push; PAT from connect-pat.lgthinq.com; [pythinqconnect SDK](https://github.com/thinq-connect/pythinqconnect)) |
| Local de-clouding (community) | Partial | LG: [rethink](https://github.com/anszom/rethink) / [ponder](https://github.com/arrudagates/ponder) emulate the ThinQ cloud on your LAN. Samsung: swap the module for an ESP32 running ESPHome (above) |
| Screen fridges and washers (Tizen) | No sideload path documented | **[unverified]** |

So a real appliance is a **control endpoint**, not a compute node. The honest ChorePilot integration runs the LLM somewhere else and has it call these cloud APIs as tools.

### 1.4 The joke-accurate targets

| Device | CPU | RAM | Storage | Wi-Fi (measured where cited) | Mem BW | Realistic best model |
|---|---|---|---|---|---|---|
| ESP32-S3 (N8R8 class) | 2× LX7 @ 240 MHz | 512 KB SRAM + 2–8 MB PSRAM | 8–16 MB flash | 802.11n; Espressif lab iperf **83 TCP TX / 73 RX Mbps** ([IDF docs](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-guides/wifi-driver/wifi-performance-and-power-save.html)); far lower in the field ([esp32.com](https://esp32.com/viewtopic.php?p=101785)) | PSRAM ~0.05–0.1 GB/s **[est]** | llama2.c TinyStories **260K f32 at 19–30 tok/s**; a 3.3M int8 model at ~12 tok/s; a 28.9M model reported at 9.9 tok/s ([DaveBben/esp32-llm](https://github.com/DaveBben/esp32-llm), [doryiii/esp32-llm](https://github.com/doryiii/esp32-llm), [Elektroda](https://www.elektroda.com/news/news4180242.html) **[unverified]**) |
| Raspberry Pi Zero 2 W | 4× Cortex-A53 @ 1 GHz (RP3A0) | 512 MB LPDDR2 | microSD | 802.11b/g/n 2.4 GHz ([RPi news](https://www.raspberrypi.com/news/new-raspberry-pi-zero-2-w/)) | ~1.5 GB/s **[est]** | ≤135M (SmolLM2-135M Q4/Q8). A 0.5B Q4 (~0.4 GB) technically fits but leaves no OS headroom **[est]** |
| Raspberry Pi 4 (4 GB) | 4× A72 @ 1.8 GHz | 1–8 GB LPDDR4-3200 | microSD/USB SSD | 802.11ac; **63.8 Mbps** iperf3 ([Tom's Hardware](https://www.tomshardware.com/news/raspberry-pi-5-wi-fi-faster)) | 12.8 GB/s theoretical, ~4–5 effective **[est]** | 1–1.5B Q4 at a few tok/s; a 3B Q4 is slow |
| Raspberry Pi 5 (8 GB) | 4× A76 @ 2.4 GHz | 2/4/8/16 GB LPDDR4X-4267 | microSD/NVMe | 802.11ac; **217 Mbps** iperf3 near the router ([Tom's Hardware](https://www.tomshardware.com/news/raspberry-pi-5-wi-fi-faster)) | 17.1 GB/s theoretical; STREAM ~10–11 GB/s ([zbotic](https://zbotic.in/raspberry-pi-5-benchmarks-real-performance-numbers-vs-pi-4/) **[unverified]**) | 1B Q4 at ~11 tok/s; 3B Q4_K_M at **4.7–6.9 tok/s** ([Stratosphere research](https://stratospherelinuxips.readthedocs.io/en/develop/immune/research_rpi_llm_performance.html)) |
| Old Android phone (4 GB, Snapdragon 8xx class) | 8-core big.LITTLE | 4 GB, of which an app realistically gets ~half | 64 GB UFS | Wi-Fi 5 | ~10–15 GB/s effective **[est]** | 1–1.5B Q4 via Termux + llama.cpp ([llama.cpp Android docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/android.md): "set context-size to a reasonable number (say, 4096)… otherwise memory could spike and kill your terminal") |
| Orange Pi 5 / RK3588 (16 GB) | 4× A76 + 4× A55, 6-TOPS NPU | 4–32 GB LPDDR4X | eMMC/NVMe | depends on board (Ethernet; Wi-Fi via module) | 33.8 GB/s theoretical, ~20 effective **[est]** | NPU via [RKLLM](https://github.com/airockchip/rknn-llm): Qwen2 0.5B **41.6 tok/s**, Qwen2.5 1.5B **16.7 tok/s**, Gemma2 2B 10.4, Gemma4 E2B 11.1 (all w8a8; [benchmark.md](https://github.com/airockchip/rknn-llm/blob/main/benchmark.md)). CPU llama.cpp can run 7–8B Q4 slowly |

Sanity check on the bandwidth model: RKLLM Qwen2.5-1.5B w8a8 uses 1.69 GB × 16.7 tok/s ≈ 28 GB/s, consistent with RK3588's 33.8 GB/s ceiling. A Pi 5 running 3B Q4_K_M reads ~2 GB × 5 tok/s ≈ 10 GB/s, which matches STREAM.

---

## 2. Running big models on small devices

### 2.1 llama.cpp RPC backend (the ChorePilot cluster path)
Sources: [tools/rpc/README.md](https://github.com/ggml-org/llama.cpp/blob/master/tools/rpc/README.md), [ggml/src/ggml-rpc/ggml-rpc.cpp](https://github.com/ggml-org/llama.cpp/blob/master/ggml/src/ggml-rpc/ggml-rpc.cpp), [ggml-rpc.h](https://github.com/ggml-org/llama.cpp/blob/master/ggml/include/ggml-rpc.h) (protocol **v8.0.0** as of today).

- **Worker:** `rpc-server -p 50052 [-c] [--device CUDA0]`. It exposes the host's accelerators, or the CPU if it has none. **`-c`** caches tensors in `$HOME/.cache/llama.cpp/rpc`.
- **Host:** `llama-cli -m model.gguf --rpc 192.168.88.10:50052,192.168.88.11:50052 -ngl 99`. Weights and KV are distributed "proportionally to each device's memory"; override with `--tensor-split`. Each device holds a contiguous run of layers, so this is pipeline/layer split.
- **Security (verbatim):** *"This example and the RPC backend are currently in a proof-of-concept development stage. As such, the functionality is fragile and insecure. **Never run the RPC server on an open network or in a sensitive environment!**"* ChorePilot should bind to the LAN interface only, never 0.0.0.0 on an untrusted network, and warn loudly.
- **Load-time upload:** the host reads the GGUF and pushes every weight tensor to its worker with `RPC_CMD_SET_TENSOR`. For weight tensors over **10 MiB** (`HASH_THRESHOLD = 10*1024*1024`), it first sends `SET_TENSOR_HASH` (an FNV hash). On a cache hit (worker started with `-c`) the bytes are skipped. **The first load ships the whole shard over Wi-Fi; later loads mostly don't.**
- **Per-token traffic:** the compute graph is serialized once. Later calls send `RPC_CMD_GRAPH_RECOMPUTE` with only a graph `uid`, served from a cache of up to `GRAPH_CACHE_MAX = 1024` graphs. Activations cross server boundaries via `COPY_TENSOR` only when both tensors are on the **same** server. Otherwise the code logs *"src and dst are on different servers, falling back to a host copy"*: a `GET_TENSOR` to the host, then a `SET_TENSOR` to the next worker. **Topology is a star through the host**, so every layer boundary costs about 2 Wi-Fi round trips.
- The source now also contains `RPC_CMD_COMM_INIT / COMM_ALLREDUCE` (collective ops) and RDMA negotiation with TCP fallback. **[unverified: how these are exposed to users]**

### 2.2 distributed-llama (b4rtaz): tensor parallel over Ethernet
[README](https://github.com/b4rtaz/distributed-llama): a root node plus workers, and node count must be **2^n**, capped at the model's KV-head count. Synchronization is per layer, so the method wants low-latency **Ethernet**. Published measurements ([discussions](https://github.com/b4rtaz/distributed-llama/discussions)):

| Setup | Model | Eval tok/s | Predict tok/s |
|---|---|---|---|
| 2× Pi 5 8GB | DeepSeek-R1-Distill 8B Q40 | 7.70 | 3.54 ([#162](https://github.com/b4rtaz/distributed-llama/discussions/162)) |
| 4× Pi 5 8GB | DeepSeek-R1-Distill 8B Q40 | 11.68 | 6.43 ([#162](https://github.com/b4rtaz/distributed-llama/discussions/162)); a third-party tuned run reports 8.32 ([Zenodo](https://zenodo.org/records/20411979) **[unverified]**) |
| 4× Pi 5 8GB, TP-Link LS1008G | Qwen3-30B-A3B Q40 (5.5 GB required) | 14.33 | 13.04 ([#255](https://github.com/b4rtaz/distributed-llama/discussions/255)) |
| 8× Pi 4B 8GB, GbE | Llama 2 70B | — | **4842 ms/token** (2122 ms inference + 2720 ms network) ([older README via fork](https://github.com/Dyl777/distributed-llama/blob/main/README.md)) |

Note the 70B row: **network was 56% of token time on wired gigabit.** Tensor parallelism over Wi-Fi would be worse (see §3).

### 2.3 Others
- **prima.cpp** ([arXiv 2504.08791](https://arxiv.org/abs/2504.08791), [OpenCPIL/prima.cpp](https://github.com/OpenCPIL/prima.cpp)). Built for home clusters "with mixed CPUs/GPUs, insufficient RAM/VRAM, slow disks, **Wi-Fi links**, and heterogeneous OSs." It uses piped-ring parallelism (overlapping mmap disk I/O with compute and communication) and the Halda scheduler. Reported: **70B at 674 ms/token** on four consumer devices with <6% memory pressure, 32B at 26 tok/s with speculative decoding, and 5–17× lower TPOT than llama.cpp, exo and dllama. This is the closest prior art to ChorePilot.
- **exo** ([exo-explore/exo](https://github.com/exo-explore/exo)): auto-discovery (Zenoh), pipeline plus tensor parallel, RDMA over Thunderbolt 5. Linux is CPU-only. Pi and Android are not documented.
- **Petals** ([bigscience-workshop/petals](https://github.com/bigscience-workshop/petals)): BitTorrent-style swarm over the internet, "up to 6 tokens/sec" Llama 2 70B and 4 tok/s Falcon 180B.
- **AirLLM** ([lyogavin/airllm](https://github.com/lyogavin/airllm)): one layer resident at a time ("70B on 4GB GPU"). Its own caveat: "the bottleneck is mainly at the disk loading". It is effectively the weight-streaming case in §3.
- **mmap + SSD/NFS:** llama.cpp mmaps GGUF by default, so a model larger than RAM pages in from disk on every token. Throughput is then disk bandwidth ÷ model bytes. Over NFS on Wi-Fi it is network bandwidth ÷ model bytes, i.e. minutes per token (§3).
- **MoE expert offload:** `--override-tensor/-ot ".ffn_.*_exps.=CPU"`, `--cpu-moe` (all experts on CPU), `--n-cpu-moe N` (the first N layers' experts on CPU) ([llama-server manpage](https://manpages.debian.org/unstable/llama.cpp-tools/llama-server.1.en.html)). On 2026-10-07, PR [#29887](https://github.com/ggml-org/llama.cpp/pull/29887) "add a GPU cache for MoE experts kept in host memory" was merged (flag reported as `--moe-cache-mib`, **[unverified flag name]**). MoE is the small-device win: Qwen3-30B-A3B touches about 3B params per token.
- **Engram (DeepSeek, verified):** "Conditional Memory via Scalable Lookup: A New Axis of Sparsity for LLMs" ([arXiv 2601.07372](https://arxiv.org/abs/2601.07372), code [deepseek-ai/Engram](https://github.com/deepseek-ai/Engram), created 2026-01-12). It adds hashed N-gram embedding tables with O(1) lookup, and replacing ~20% of MoE params with lookups improves quality. Lookup tables are a natural fit for NVMe or a LAN node: per-token reads are a few rows, not whole matrices. Runtime support in llama.cpp for Engram-style tables: **[unverified / not found]**.

---

## 3. Bandwidth and latency math (27B dense over Wi-Fi)

### 3.1 Model facts (from the HF `config.json` files)
| Model | hidden | layers | attn heads / KV heads | head_dim | notes |
|---|---|---|---|---|---|
| Qwen3.5-27B ([config](https://huggingface.co/Qwen/Qwen3.5-27B/raw/main/config.json)) | **5120** | 64 | 24 / 4 | 256 | hybrid: `full_attention_interval: 4`, so 16 full-attention and 48 linear-attention layers |
| Gemma 3 27B ([config mirror](https://huggingface.co/unsloth/gemma-3-27b-it/raw/main/config.json)) | **5376** | 62 | 32 / 16 | 128 | sliding window 1024, pattern 6 (≈10 global layers) |
| Qwen3-32B ([config](https://huggingface.co/Qwen/Qwen3-32B/raw/main/config.json)) | 5120 | 64 | 64 / 8 | 128 | dense, all full attention |

### 3.2 Weight size vs per-token traffic
Weights = 27e9 × bpw / 8.

| bpw | Weights | Stream weights per token, Wi-Fi 4 (5 MB/s) | Wi-Fi 5 (37.5 MB/s) | Wi-Fi 6 (75 MB/s) |
|---|---|---|---|---|
| 2.0 | 6.75 GB | 22.5 min | 3.0 min | 1.5 min |
| 3.0 | 10.1 GB | 33.8 min | 4.5 min | 2.25 min |
| 4.0 | 13.5 GB | 45 min | 6.0 min | 3.0 min |
| 4.85 (Q4_K_M) | 16.4 GB | 55 min | 7.3 min | 3.6 min |

**Pipeline (layer-split) RPC instead sends activations.** One hidden-state vector per token per boundary is 5120 × 4 B (f32) = **20 KiB** (21 KiB for Gemma's 5376). That is 0.55 ms at 300 Mbps and 4 ms at 40 Mbps. **Latency dominates, not bandwidth.**

Per-token network cost with llama.cpp's star topology **[est]**: for N workers, about 2N round trips (graph-recompute dispatch, then a get/set of the boundary activation via the host). At Wi-Fi RTT 2–10 ms, N = 3 gives **12–60 ms/token** of pure network: a 17–80 tok/s ceiling from the network alone. Compute is far slower than that, so network is not the bottleneck for single-stream pipeline decode.

**Tensor parallel over Wi-Fi is the trap [est].** It needs about 2 all-reduces per layer, so 64 layers make 128 sync points. At 5 ms RTT that is ~0.64 s/token of latency floor before any compute. That is why distributed-llama insists on Ethernet: even on GbE its 70B run spent 2.7 s/token in network.

### 3.3 Memory-bandwidth-bound decode
tok/s ≈ effective_mem_BW ÷ bytes_read_per_token, and for dense models bytes_read ≈ weight size. Pipeline split does **not** parallelise single-stream decode: the token visits each node in turn, so time = Σ(shard_i / BW_i) + network. Pipelining only adds memory capacity.

| Scenario (27B dense) | bpw | Compute time/token | Net (3 hops, 5 ms) | ≈ tok/s |
|---|---|---|---|---|
| 1× Pi 5 16GB, local (10 GB/s) | 3.0 (10.1 GB) | 1.01 s | 0 | **~1.0** |
| 2× Pi 5 8GB, RPC pipeline | 4.0 (13.5 GB) | 1.35 s | ~20 ms | **~0.73** |
| 3× Pi 5 8GB, RPC pipeline | 2.0 (6.75 GB) | 0.68 s | ~30 ms | **~1.4** |
| Pi 5 + RK3588 (20 GB/s) + old phone (12 GB/s), split ∝ RAM | 4.0 | ~0.9 s | ~30 ms | **~1.1** |
| 4× Pi 5, tensor parallel over **Wi-Fi** | 4.0 | 0.34 s | ~0.64 s (128 syncs) | **~1.0** |
| 4× Pi 5, tensor parallel over **GbE** (0.2 ms) | 4.0 | 0.34 s | ~26 ms | ~2.7 (theoretical; real dllama lower) |
| Stream weights over Wi-Fi 5 (AirLLM/NFS style) | 4.0 | — | 360 s | **0.003** |
| Pi Zero 2 W cluster (1.5 GB/s, 0.4 GB usable each) | 2.0 | 4.5 s, needs ~17 nodes | ~0.2 s | ~0.2 |

All rows are **[est]** from the model above. Prefill is compute-bound and much faster per token. Real llama.cpp CPU decode reaches about 60–80% of STREAM bandwidth.

---

## 4. Picking a model for a device

### 4.1 Memory budget
`total = weights + KV + compute_buffer + OS_headroom`

- **KV cache** = 2 × n_layers_with_KV × n_kv_heads × head_dim × ctx × bytes (f16 = 2).
  - Qwen3-32B: 2·64·8·128·2 = **256 KiB/token**, so 2 GiB at 8k ctx.
  - Qwen3.5-27B: only 16 full-attention layers, 2·16·4·256·2 = **64 KiB/token**, so 512 MiB at 8k, plus a fixed linear-attention state (~0.15 GB **[est]**).
  - Gemma 3 27B at 8k: global 10 layers ≈ 671 MB plus local 52 layers × 1024 window ≈ 436 MB, about **1.1 GB**.
  - `-ctk q8_0 -ctv q8_0` halves this.
- **Compute buffer:** ~0.1–0.5 GB for small models, ~0.5–1.5 GB for 27B at 4–8k **[est]**.
- **OS headroom:** Linux SBC ~0.3–0.5 GB. Android: an app reliably gets about **half** of physical RAM (this matches the Pollard calculator's `--device phone` rule, see 4.3).

### 4.2 Quant bits per weight
Nominal block bpw comes from ggml's block layouts. Effective whole-file bpw is from llama.cpp's own [quantize README](https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md) table for Llama-3.1-8B, which includes higher-precision embedding and output tensors. Small models have a larger embedding share, so their effective bpw runs higher.

| Type | Nominal bpw | Effective bpw (Llama-3.1-8B file) | 8B file size |
|---|---|---|---|
| Q2_K | 2.625 | 3.16 | 2.95 GiB |
| IQ3_XXS | 3.0625 | 3.25 | 3.04 GiB |
| Q3_K_M | 3.4375 (Q3_K) mix | 4.00 | 3.74 GiB |
| IQ4_XS | 4.25 | 4.46 | 4.17 GiB |
| Q4_K_M | 4.5 (Q4_K) mix | 4.89 | 4.58 GiB |
| Q5_K_M | 5.5 (Q5_K) mix | 5.70 | 5.33 GiB |
| Q6_K | 6.5625 | 6.56 | 6.14 GiB |
| Q8_0 | 8.5 | 8.50 | 7.95 GiB |
| F16 | 16 | 16.0 | 14.96 GiB |

Your brief's figures (Q2_K ≈ 2.6, IQ3_XXS ≈ 3.06, Q3_K_M ≈ 3.9, IQ4_XS ≈ 4.25, Q4_K_M ≈ 4.85, Q5_K_M ≈ 5.7, Q6_K ≈ 6.56, Q8_0 ≈ 8.5) mix nominal and effective values. **The code should size files with the effective column**, or better, read the real GGUF size.

### 4.3 Pollard as the "will it fit" oracle
- **pollard-calc**, in the public pollard-weights project, is documented as answering "will this model fit your RAM, and how fast? Byte-economics from the config alone (pure stdlib, no GPU)". Example: `pollard-calc --model Qwen/Qwen3-30B-A3B --ram 16`. With `--ctx N` it adds a run-time pre-flight (KV cache, total RAM, go/no-go) and a `--device gpu|unified|phone` switch ("a phone only gives an app ~half its RAM"). ChorePilot's planner follows the same approach: config → bytes → fit, then bandwidth → tok/s.
- **PollardWeights on HF** ([huggingface.co/PollardWeights](https://huggingface.co/PollardWeights)) has 24 public repos as of today, with mixed-precision measured-allocation GGUFs. Ones that fit small devices: `Qwen2.5-1.5B-Instruct-Pollard`, `Qwen2.5-Coder-1.5B-Instruct-Pollard`, `K2-Horizon-0.9B-Pollard`, `MiniCPM5-2B-Pollard`, `Spark-X2.5-4B-Pollard`, `Ling-3.0-tiny-Pollard`. For clusters: `Qwen3-30B-A3B-Pollard`, `LFM2.5-24B-A2B-Pollard` (MoE, good for small nodes), and the 27B dense targets `Qwen3.8-27B-Pollard`, `Penjing-27B-Pollard`, `Carnice-V3-27b-Pollard`.

### 4.4 Rule-of-thumb recommendations **[est]**
| Usable RAM | Local pick | Expected decode |
|---|---|---|
| < 8 MB (MCU) | llama2.c 260K–15M | 5–30 tok/s |
| 0.4 GB | 135M Q8 / 360M Q4 | 3–8 tok/s on a Zero 2 W |
| 2 GB | 1–1.5B Q4_K_M | 5–12 tok/s on a Pi 5 / phone |
| 4–6 GB | 3–4B Q4_K_M, or Qwen3-30B-A3B across a cluster | 4–7 tok/s |
| 8–12 GB | 7–8B Q4_K_M; a 30B-A3B IQ3 fits a 16 GB board | 2–3 tok/s (dense 8B on a Pi 5) |
| cluster ≥ 14 GB total | 27B at 2–4 bpw via RPC pipeline | 0.7–1.5 tok/s on Pis |

---

## 5. What's actually possible

**A real appliance:** no on-device LLM. The compute is a Cortex-M4/R4 Wi-Fi MCU with 0.4–4 MB of RAM, connected to the mainboard over a 9600-baud UART. The firmware is vendor-controlled. The supported control path is the cloud APIs (Home Connect, SmartThings, ThinQ Connect), or community local-cloud replacements. Swap the module for an ESP32-S3 with PSRAM and it can run a **260K–15M-param llama2.c model at a few to ~30 tok/s** while toggling the appliance over UART. That is real, and it writes TinyStories, not dishwasher strategy. Screen fridges (2.5–3 GB, Tizen) have the RAM for a 0.5–1B model but no documented way to run your code.

**Pi in the dishwasher:** a Pi 5 8GB runs **1–4B Q4 locally at ~5–11 tok/s**. Added as a llama.cpp `rpc-server` on the home LAN, it can help host a **27B at 2–4 bpw**. Decode is then bounded by Σ(shard ÷ node bandwidth), which effectively means the slowest node, plus ~2 Wi-Fi round trips per worker per token, landing around **0.7–1.5 tok/s** on Pis. The first load uploads every shard over Wi-Fi (13.5 GB at 4 bpw ≈ 6 min on Wi-Fi 5); later loads with `-c` hit the hash cache. Never expose `rpc-server` beyond a trusted LAN. For a usable big model on weak nodes, prefer an **MoE** (Qwen3-30B-A3B reaches 13 tok/s on 4× Pi 5 over Ethernet) over a 27B dense.

---

## 6. The "brain" tier: where the model that drives the appliance should live

*Added 2026-10-20.* Sections 1–5 are about squeezing a model into the dishwasher. To *control*
the dishwasher (see [CONTROL.md](CONTROL.md)), the model can live on any box on the LAN. This
section is about making that box fast.

### 6.1 Hardware (vendor spec pages)

| Device | Memory | Bandwidth | Source |
|---|---|---|---|
| Raspberry Pi 5 | up to 16 GB LPDDR4X-4267 | ~17 GB/s **[est]** (32-bit × 4267 MT/s) | [raspberrypi.com](https://www.raspberrypi.com/products/raspberry-pi-5/) |
| Jetson Orin Nano Super | 8 GB LPDDR5 | **102 GB/s** | [NVIDIA Jetson Orin](https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-orin/) |
| Jetson AGX Orin 64GB | 64 GB LPDDR5 | **204.8 GB/s** | same page |
| Jetson AGX Thor (T5000) | 128 GB LPDDR5X | **273 GB/s** | [NVIDIA Jetson Thor](https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-thor/) |
| DGX Spark (GB10) | 128 GB LPDDR5X unified | **273 GB/s** | [NVIDIA DGX Spark](https://www.nvidia.com/en-us/products/workstations/dgx-spark/) |
| Ryzen AI Max+ 395 (Strix Halo) | up to 128 GB LPDDR5X-8000, 256-bit | 256 GB/s **[est]** from the bus width (AMD's page did not load; secondary sources agree) | — |
| Mac mini M4 / M4 Pro | — | 120 / 273 GB/s | [llama.cpp #4167](https://github.com/ggml-org/llama.cpp/discussions/4167) |
| Mac Studio M4 Max / M3 Ultra | up to 128 / 512 GB | 546 / ~800 GB/s | [Apple newsroom](https://www.apple.com/newsroom/2025/03/apple-unveils-new-mac-studio-the-most-powerful-mac-ever/), #4167 |
| Current Mac mini (M6 / M5 Pro) | 16–32 GB / from 24 GB | 153–170 / 307 GB/s | [apple.com/mac-mini/specs](https://www.apple.com/mac-mini/specs/) |
| Current Mac Studio (M5 Max / M5 Ultra) | 36–128 / 96–512 GB | 460–614 / 1200 GB/s | [apple.com/mac-studio/specs](https://www.apple.com/mac-studio/specs/) |

Presets with these numbers ship in `profiles.py`: `jetson-orin-nano-super-8g`,
`jetson-agx-orin-64g`, `jetson-agx-thor-128g`, `dgx-spark-128g`, `strix-halo-128g` and
`mac-mini-m4-pro-64g`. Try `chorepilot recommend --profile dgx-spark-128g --catalog`.

### 6.2 Measured single-stream decode (tok/s)

| Device | 30B-A3B-class MoE | Dense 27–32B | Small (1.7–8B) | Source |
|---|---|---|---|---|
| DGX Spark | Qwen3-Coder-30B-A3B Q8_0 **44.3**, falling to 26.9 at 32K depth; Qwen3-30B-A3B INT4 **81.1** (TensorRT Edge-LLM); gpt-oss-120b MXFP4 **60.5**; gpt-oss-20b **62–80** | Qwen3.5-27B INT4 AWQ **13.9**; Gemma 3 27B Q4_K_M 10.8 (Ollama) **[unverified snippet]** | Qwen3-4B **73.9**, Qwen3-1.7B **133.9** | [llama.cpp #16578](https://github.com/ggml-org/llama.cpp/discussions/16578), [Edge-LLM benchmarks](https://nvidia.github.io/TensorRT-Edge-LLM/latest/user_guide/performance/performance-benchmarks.html), [JetsonHacks](https://jetsonhacks.com/wp-content/uploads/2025/10/SparkThorLlamaBenchmarks.html), [LMSYS](https://www.lmsys.org/blog/2025-10-13-nvidia-dgx-spark) |
| Jetson AGX Thor | Qwen3-30B-A3B **77–84**; Nemotron-3-Nano-30B-A3B **76.2**; gpt-oss-120b 41.8 (llama.cpp) | Qwen3.5-27B INT4 **15.2** | Qwen3-4B **76.5** | Edge-LLM, JetsonHacks |
| Jetson AGX Orin 64GB | not published (~45–60 **[est]**) | Qwen3.5-27B INT4 **10.5** | Qwen3-8B **30.6**, Qwen3-4B **50.7** | Edge-LLM |
| Jetson Orin Nano Super 8GB | doesn't fit | doesn't fit | Llama 3.2 3B **43.1**, Qwen2.5 7B **21.8** (MLC INT4); Qwen3-1.7B 32.3 | [Jetson AI Lab](https://www.jetson-ai-lab.com/archive/benchmarks.html), Edge-LLM |
| Strix Halo 128GB | Qwen3-30B-A3B Q4 **66–86** (Vulkan); Qwen3.5-35B-A3B Q8_0 **53.5**; gpt-oss-120b 36–53 | Qwen3.5-27B Q8_0 **7.8**; Qwen3-32B Q8_0 **6.4** (Q4 ≈ 12–13 **[est]**) | Llama-2-7B Q4_0 **52.7** | [Level1Techs](https://forum.level1techs.com/t/strix-halo-ryzen-ai-max-395-llm-benchmark-results/233796), [visorcraft](https://github.com/visorcraft/strix-halo-llm-perf), [llm-tracker](https://llm-tracker.info/_TOORG/Strix-Halo), [inference notes](https://forum.level1techs.com/t/strix-halo-llm-inference-notes/249466) |
| Mac (llama.cpp, LLaMA-7B Q4_0) | M4 Pro ~45–60 **[est]** | M4 Pro ~10, M4 Max ~18 **[est]** | M4 **24.1**, M4 Pro **49.6**, M4 Max **83.1**, M3 Ultra **92.1** | #4167 |
| **This repo's Mac** (M-series, 17 GB, busy) | — | — | Qwen2.5-1.5B Pollard Q4_K_S **74–89** (measured below) | §7 |

**Takeaways:**

- **The MoE is the sweet spot.** A 30B-A3B-class MoE (3.3B active of 30.5B,
  [Qwen3-30B-A3B card](https://huggingface.co/Qwen/Qwen3-30B-A3B)) runs at 50–85 tok/s on
  every ≥200 GB/s box. gpt-oss-20b (3.6B active), LFM2.5-24B-A2B (2B active) and
  Granite-4.0-H-Tiny (1B active) are the small-active alternatives.
- **"Usable 27B" means a dense 27B at ~10–15 tok/s on a 273 GB/s box.** That is fine for
  appliance control, where answers are short. It gets to ~20–30 tok/s only with speculative
  decoding (6.3).
- **Below 16 GB, run a 1.5–4B instruct model.** Appliance control needs ~20–60 output tokens
  per turn, so even a Pi 5 is usable for it. A 27B fits only at IQ1/IQ2 (6.4).

The planner reproduces these numbers: `0.7 × 273 GB/s ÷ 16.4 GB` (27B Q4_K_M) gives
**11.6 tok/s**, against 10.8–13.9 measured.

### 6.3 Speculative decoding in llama.cpp (as of this build, b10360)

**Flag renames.** `llama-server --help` on this Mac lists
`--spec-type none,draft-simple,draft-eagle3,draft-mtp,draft-dflash,draft-dspark,ngram-simple,ngram-map-k,ngram-map-k4v,ngram-mod,ngram-cache`.
It also says `--draft-max` "has been removed; use --spec-draft-n-max". The rename landed in
[#22964](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).
`chorepilot serve --draft/--spec/--draft-max` reads `--help` and emits whichever flags the
local build understands.

| Method | Status | Reported speed-up |
|---|---|---|
| Draft model (`-md small.gguf`) | long-standing | depends on acceptance; same tokenizer needed |
| **MTP** (`--spec-type draft-mtp`, head inside the GGUF) | merged, [#22673](https://github.com/ggml-org/llama.cpp/pull/22673) (2026-05-16) | RTX 3090 23→42.5 tok/s (1.85×); DGX Spark 7.0→18.0 **[model unstated]**; acceptance 0.72 at 3 drafts |
| **EAGLE-3** | merged, [#18039](https://github.com/ggml-org/llama.cpp/pull/18039) (2026-06-12) | PR claims 2–3× |
| **DFlash** (block-diffusion drafter) | [arXiv 2602.06036](https://arxiv.org/abs/2602.06036), [z-lab/dflash](https://github.com/z-lab/dflash); in llama.cpp | 5.15× on Qwen3-8B on datacenter GPUs (τ = 6.54). **But on Jetson Thor (Edge-LLM) Nemotron-30B-A3B ran 81.6 vanilla → 95.3 with MTP → 58.7 with DFlash**: slower than no speculation on a bandwidth-bound edge MoE |
| n-gram (`ngram-mod`) | in llama.cpp | no draft model; helps repetitive output such as JSON tool calls and code |

**Rule of thumb:** use MTP or EAGLE-3 on dense 27B-class models (≈1.7–2.5×). On small-active
MoEs it is often not worth it. Benchmark DFlash on *your* box before trusting the paper.

### 6.4 Extreme quant rungs (1–2 bit)
Whole-file bpw on Llama-3.1-8B, from the llama.cpp
[quantize README](https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md):

| Quant | bpw |
|---|---|
| IQ1_S | 2.00 |
| IQ1_M | 2.15 |
| IQ2_XXS | 2.38 |
| IQ2_XS | 2.59 |
| IQ2_S | 2.74 |
| IQ2_M | 2.93 |

Quality falls off steeply below ~2.5 bpw, and these quants need an imatrix.

PollardWeights low-bit rungs, from the HF API on 2026-10-20:

| Model | Rungs |
|---|---|
| Qwen3-30B-A3B | IQ1_KT 7.54 GB |
| Penjing-27B | IQ1_KT 6.53, IQ2_KT 7.36, IQ2_XXS 7.25 GB |
| LFM2.5-24B-A2B | IQ1_KT 5.82, IQ2_XXS 8.89 GB |
| Qwen2.5-14B | IQ1_KT 3.92 GB |
| FrogMini-14B | IQ2_XXS 4.56, IQ2_S 5.94 GB |
| clef-flash | IQ2_XXS 3.35 GB |

**`*_KT` files load only in ik_llama.cpp, not in stock llama.cpp.** These rungs are how a 27B
or 30B-A3B gets onto a 8–12 GB board. The bpw math in §3 still applies: an IQ1_KT 27B
(6.5 GB) on a Pi 5 reads 6.5 GB per token, about 1.5 tok/s **[est]**.

### 6.5 Tool calling with llama-server

- **`--jinja`.** Tool calls go through the model's Jinja chat template. In this build
  `--jinja` is on by default ("default: enabled" in `--help`); older builds need the flag,
  and ChorePilot passes it whenever `--help` lists it.
- **Native parsers.** Llama 3.x, Functionary, Hermes 2/3, Qwen 2.5 (Hermes-2-Pro format),
  Mistral Nemo, Firefunction v2, Command R7B, DeepSeek R1 and gpt-oss (Harmony). Other
  templates fall back to a generic, more verbose format
  ([function-calling.md](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)).
- **`tool_choice`.** The OpenAI endpoint accepts only `auto`, `none` and `required`.
  ChorePilot uses `auto`.
- **Parse fallback.** If a `<tool_call>{…}</tool_call>` block is left in the text, the bridge
  parses it anyway.

---

## 7. Measured: the live control demo on this Mac

The run used the `simulated` backend, Pollard
[Qwen2.5-1.5B-Instruct Q4_K_S](https://huggingface.co/PollardWeights/Qwen2.5-1.5B-Instruct-Pollard)
(0.94 GB) and Homebrew llama.cpp b10360. The Mac was loaded, with ~5.3 GB usable. Each run
started its own server, served the request and unloaded.

| Request | Tool calls the model made | Bridge result |
|---|---|---|
| "wash my dishes" `--dry-run` | `start_program{program: auto}` | dry run; printed the write it would send |
| "wash my dishes" (no TTY, no `--yes`) | `start_program{auto}` | **declined**: nothing started, and the model told the user it needs confirmation |
| "wash my dishes" `--yes` | `start_program{auto}` | running, 2 h 29 min |
| "run an eco wash and tell me when it's done" | `start_program{eco}`, `notify_when_done` | running, plus a watcher spawned |
| "pause the dishwasher for a sec" | `pause{}` | paused |
| "never mind, stop the wash completely" | `cancel{}` | ready |
| "quick wash please, I'm in a hurry" (door open) | `start_program{quick}` | refused: "the door is open; close it first" |
| "quick wash, half load, I'm in a hurry" | `start_program{quick, options:{half_load:true}}` | refused by the rate limiter (60 s between starts) |
| "hey, wash my dishes and ping me when they're done" | `start_program{auto}`, `notify_when_done` | finished; watcher printed "The dishes are done." |

- **Speed.** Generation ran at **74–89 tok/s**, prompt processing at ~1000 tok/s (an 850-token
  tool prompt took ~0.8 s). A request took **1.5–1.9 s** of model time over two turns, and
  `wash` took **5.0 s wall** including model load and unload.
- **Tool selection** was right in all 9 runs, with zero malformed calls.
- **Weakness.** The 1.5B model's prose sometimes garbles numbers: early runs read
  `remaining_s` as hours. Status now also carries a human `remaining: "2 h 29 min"` string,
  which fixed it. It was also vague on a status-only question. Use ≥3B, or a 30B-A3B on a
  brain-tier box, for chattier answers.
