"""Stdlib unittest suite: `python3 -m unittest discover tests`.

Builds a tiny synthetic GGUF on the fly so the header reader, the fit math, the cluster
planner and the guard are all tested without downloading anything.
"""
import io
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chorepilot.fit import ModelSpec, best_ctx, best_rung, fit_local, plan_cluster  # noqa: E402
from chorepilot.gguf import read_gguf  # noqa: E402
from chorepilot.guard import Watchdog, floor_gb  # noqa: E402
from chorepilot.profiles import PRESETS, Device, load_profile  # noqa: E402


def _s(x: str) -> bytes:
    b = x.encode()
    return struct.pack("<Q", len(b)) + b


def make_gguf(n_layers=4, n_embd=64, n_vocab=1000, n_head=4, n_head_kv=2, ftype=8):
    """A header-only GGUF (tensor data omitted) with llama-like geometry, Q8_0 tensors."""
    kv = [
        ("general.architecture", 8, _s("llama")),
        ("general.name", 8, _s("tiny-test")),
        ("llama.block_count", 4, struct.pack("<I", n_layers)),
        ("llama.embedding_length", 4, struct.pack("<I", n_embd)),
        ("llama.context_length", 4, struct.pack("<I", 4096)),
        ("llama.attention.head_count", 4, struct.pack("<I", n_head)),
        ("llama.attention.head_count_kv", 4, struct.pack("<I", n_head_kv)),
        ("tokenizer.ggml.tokens", 9, struct.pack("<IQ", 8, 2) + _s("a") + _s("b")),
    ]
    tensors = [("token_embd.weight", [n_embd, n_vocab])]
    for i in range(n_layers):
        tensors += [(f"blk.{i}.attn_q.weight", [n_embd, n_embd]),
                    (f"blk.{i}.ffn_up.weight", [n_embd, n_embd * 4])]
    tensors.append(("output.weight", [n_embd, n_vocab]))
    out = io.BytesIO()
    out.write(b"GGUF" + struct.pack("<IQQ", 3, len(tensors), len(kv)))
    for k, t, v in kv:
        out.write(_s(k) + struct.pack("<I", t) + v)
    for name, dims in tensors:
        out.write(_s(name) + struct.pack("<I", len(dims)) + b"".join(struct.pack("<Q", d) for d in dims)
                  + struct.pack("<IQ", ftype, 0))
    return out.getvalue(), tensors


class GGUFTests(unittest.TestCase):
    def test_header(self):
        raw, tensors = make_gguf()
        n = sum(a * b for _, (a, b) in tensors)
        info = read_gguf(io.BytesIO(raw), file_bytes=n * 34 // 32)
        self.assertEqual(info.arch, "llama")
        self.assertEqual(info.n_layers, 4)
        self.assertEqual(info.n_params, n)
        self.assertEqual(info.n_vocab, 1000)
        # K+V * kv_heads * head_dim * f16, per layer
        self.assertEqual(info.kv_bytes_per_token(), 4 * 2 * (2 * 16) * 2)
        self.assertAlmostEqual(info.bpw, 8.5, places=2)
        self.assertGreater(info.layer_bytes, 0)

    def test_file_roundtrip(self):
        raw, _ = make_gguf()
        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as f:
            f.write(raw)
        try:
            self.assertEqual(read_gguf(f.name).name, "tiny-test")
        finally:
            os.unlink(f.name)


def spec(params_b=0.5, bpw=4.5, layers=24, embd=896, vocab=151936, kv=12288):
    fb = int(params_b * 1e9 * bpw / 8)
    emb = vocab * embd * bpw / 8
    return ModelSpec(name="t", n_params=int(params_b * 1e9), file_bytes=fb, n_layers=layers,
                     n_embd=embd, n_vocab=vocab, kv_per_token=kv, active_bytes=fb,
                     per_layer_bytes=(fb - emb) / layers, nonlayer_bytes=int(emb),
                     n_ctx_train=32768, quant="IQ4_XS")


class FitTests(unittest.TestCase):
    def test_small_model_on_dishwasher(self):
        f = fit_local(spec(), PRESETS["dishwasher-1g"], 2048)
        self.assertTrue(f.fits, f)
        self.assertGreater(f.tok_s, 1)

    def test_27b_never_fits_dishwasher(self):
        f = fit_local(spec(27, 4.0, 64, 5120, 248320, 65536), PRESETS["dishwasher-1g"], 2048)
        self.assertFalse(f.fits)

    def test_appliance_module_cannot_run(self):
        f = fit_local(spec(0.001, 4), PRESETS["smart-appliance-module"], 256)
        self.assertEqual(f.mode, "no")

    def test_best_ctx_monotonic(self):
        big = best_ctx(spec(), PRESETS["pi-5-8gb"])
        small = best_ctx(spec(), PRESETS["pi-zero-2w"])
        self.assertGreaterEqual(big.ctx, small.ctx)

    def test_rung_is_smaller_than_source(self):
        m = spec(8, 4.89, 32, 4096, 128256, 131072)
        m.quant = "Q4_K_M"
        r = best_rung(m, PRESETS["pi-4-4gb"], 2048)
        self.assertIsNotNone(r)
        self.assertLess(r.model.file_bytes, m.file_bytes)


class ClusterTests(unittest.TestCase):
    def test_split_respects_budgets(self):
        m = spec(27, 3.0, 64, 5120, 248320, 65536)
        host = load_profile("pi-5-8gb")
        plan = plan_cluster(m, host, [load_profile("pi-5-8gb"), load_profile("dishwasher-1g")], 4096,
                            strategy="proportional")
        self.assertTrue(plan.fits)
        self.assertEqual(sum(n.layers for n in plan.nodes), 64)
        for n in plan.nodes:
            self.assertLessEqual(n.total_gb, n.usable_gb + 1e-6, n.device.name)
        self.assertGreater(plan.nodes[2].layers, 0)  # the dishwasher does its share

    def test_cluster_too_small(self):
        m = spec(27, 4.0, 64, 5120, 248320, 65536)
        plan = plan_cluster(m, load_profile("pi-4-4gb"), [load_profile("dishwasher-1g")], 4096)
        self.assertFalse(plan.fits)
        self.assertEqual(plan.tok_s, 0.0)


class GuardTests(unittest.TestCase):
    def test_floor(self):
        self.assertAlmostEqual(floor_gb(1.0), 0.15)
        self.assertAlmostEqual(floor_gb(64.0), 1.0)

    def test_watchdog_kills_child_on_low_memory(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        dog = Watchdog(proc, floor=1.0, interval=0.05, strikes=2, log=lambda *_: None,
                       sampler=lambda: 0.5)  # pretend the machine is starving
        dog.start()
        t = time.time()
        while proc.poll() is None and time.time() - t < 10:
            time.sleep(0.05)
        self.assertTrue(dog.tripped)
        self.assertIsNotNone(proc.poll())

    def test_device_usable_leaves_reserve(self):
        d = Device(name="x", ram_gb=1.0)
        self.assertLess(d.usable_gb(), 1.0)
        self.assertGreater(d.usable_gb(), 0.5)


if __name__ == "__main__":
    unittest.main()
