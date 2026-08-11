# -*- coding: utf-8 -*-
"""初始化验证：用内置样例复刻并对比参考图（MAE），确认环境就绪。

用法:
    python run_example.py [repro_anima_00015|repro_sofa_rose|all]

流程: 读 examples/<name>.json 的完整元数据（提示词/负面/seed/参数/LoRA）
→ generate 复刻 → 与 examples/ref_<name>.png 逐像素对比 MAE。
MAE 小（< 10）说明模型/LoRA/管线与参考环境一致；差异大先检查
模型与 LoRA 文件是否齐全（server_info 可查）。
"""
import sys, io, json
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import logging
logging.disable(logging.CRITICAL)
import good_comfyui_mcp as m

ROOT = Path(__file__).parent
EX = ROOT / "examples"
NAMES = ["repro_anima_00015", "repro_sofa_rose"]


def compare_mae(a: str, b: str) -> float:
    from PIL import Image
    import numpy as np
    ia = Image.open(a).convert("RGB")
    ib = Image.open(b).convert("RGB")
    if ia.size != ib.size:
        ib = ib.resize(ia.size)
    return round(float(np.abs(np.asarray(ia).astype(float) - np.asarray(ib).astype(float)).mean()), 2)


def run(name: str) -> None:
    cfg = json.loads((EX / f"{name}.json").read_text(encoding="utf-8"))
    print(f"===== {name}: {cfg.get('description', '')} =====")
    r = m.generate(
        prompt=cfg["prompt"],
        negative_prompt=cfg.get("negative_prompt", ""),
        seed=cfg.get("seed"),
        width=cfg.get("width"), height=cfg.get("height"),
        steps=cfg.get("steps"), cfg=cfg.get("cfg"),
        sampler_name=cfg.get("sampler_name"),
        lora_text=cfg.get("lora_text", ""),
        timeout=1200,
    )
    out = r.get("outputs", [])
    if not out:
        print("生成失败:", r)
        return
    # 输出是 ComfyUI 相对路径（可能带 output/ 前缀）
    p0 = str(out[-1]).replace("\\", "/")
    if p0.startswith("output/"):
        src = m.OUTPUT_DIR.parent / p0
    else:
        src = m.OUTPUT_DIR / p0
    mae = compare_mae(str(src), str(EX / f"ref_{name}.png"))
    status = "✅ 环境一致" if mae < 10 else ("⚠️ 接近" if mae < 25 else "❌ 差异大")
    print(f"  输出: {src}")
    print(f"  与参考图 MAE: {mae}  {status}")
    print(f"  说明: MAE≈0 表示完全复现；差异大请检查模型/LoRA 是否齐全（server_info）")


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else ["all"]
    if "all" in targets:
        targets = NAMES
    for t in targets:
        if t in NAMES:
            run(t)
        else:
            print(f"未知样例: {t}（可选 {NAMES} 或 all）")
