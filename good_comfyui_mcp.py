#!/usr/bin/env python3
"""comfyUI-lite MCP server.

Drives a local ComfyUI through a fixed pipeline workflow:
  1. lookup_character_tags()  - query Danbooru for a character's canonical tags
                                (via the camofox-browser HTTP API, cached locally)
  2. generate()               - run the default pipeline with a user-approved prompt

Usage:  python good_comfyui_mcp.py   (stdio MCP server)

Env overrides:
  PIPELINE      path to the workflow JSON (default ./pipeline.json)
  COMFYUI_URL   base URL of ComfyUI     (default http://127.0.0.1:8188)
  CAMOFOX_URL   base URL of camofox-browser (default http://127.0.0.1:9377)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import random
import shutil
import struct
import subprocess
import sys
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

PIPELINE = Path(os.environ.get("PIPELINE", str(Path(__file__).parent / "pipeline.json")))

# 默认 5 件套 LoRA（generate 不传 lora_text 时自动挂载；传 "" 则空载）
DEFAULT_LORAS = [
    ("ushikani_kassen_lora-000013.safetensors", 0.3),
    ("anima-darklight-style-v1-000194.safetensors", 0.3),
    ("anima-base-1-photo-background-v4.safetensors", 0.6),
    ("RealSkin SliderV2.safetensors", 0.8),
    ("surtr945_v1.safetensors", 0.8),
]
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
COMPARE_DIR = Path(__file__).parent / "compare"
VIEW_BASE = "http://127.0.0.1:8899"
CAMOFOX_URL = os.environ.get("CAMOFOX_URL", "http://127.0.0.1:9377").rstrip("/")
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_TTL_DAYS = 30

CAMOFOX_USER = "good_comfyui_mcp"
CAMOFOX_SESSION = "main"

mcp = FastMCP("good-comfyui-mcp")
_http = httpx.Client(timeout=30, trust_env=False)


# ---------------------------------------------------------------- camofox lifecycle

def camofox_healthy() -> bool:
    try:
        return _http.get(f"{CAMOFOX_URL}/health", timeout=5).json().get("ok") is True
    except Exception:
        return False


def _launch_camofox() -> None:
    """Start camofox-browser in the background if it is installed but not running."""
    cmd = shutil.which("camofox-browser") or shutil.which("camofox-browser.cmd")
    if not cmd:
        raise RuntimeError("camofox-browser not found in PATH; install it or start it manually")
    shim_dir = Path(cmd).resolve().parent
    js = shim_dir / "node_modules" / "@askjo" / "camofox-browser" / "bin" / "camofox-browser.js"
    if not js.exists():
        raise RuntimeError(f"camofox-browser entry not found: {js}")
    node = shutil.which("node") or str(Path(sys.executable).parent / "node.exe")
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    subprocess.Popen([node, str(js)], cwd=str(shim_dir),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags, close_fds=True)


def ensure_camofox(timeout: float = 60) -> str:
    """Return 'online' or 'launched'; raise after timeout if it never comes up."""
    if camofox_healthy():
        return "online"
    _launch_camofox()
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        if camofox_healthy():
            return "launched"
    raise RuntimeError(f"camofox-browser did not become healthy within {timeout:.0f}s")


# ---------------------------------------------------------------- camofox

def camofox_tab() -> str:
    """Create a dedicated tab (unique session key so concurrent calls do not
    interfere). Callers must close it with camofox_close()."""
    session_key = f"{CAMOFOX_USER}-{uuid.uuid4().hex[:8]}"
    r = _http.post(f"{CAMOFOX_URL}/tabs", json={
        "userId": CAMOFOX_USER, "sessionKey": session_key,
    })
    r.raise_for_status()
    return r.json()["tabId"]


def camofox_close(tab_id: str) -> None:
    try:
        _http.delete(f"{CAMOFOX_URL}/tabs/{tab_id}",
                     params={"userId": CAMOFOX_USER})
    except Exception:
        pass


def camofox_navigate(tab_id: str, url: str) -> None:
    _http.post(f"{CAMOFOX_URL}/tabs/{tab_id}/navigate", json={
        "userId": CAMOFOX_USER, "url": url,
    }).raise_for_status()
    # wait for the page to finish loading instead of a fixed sleep
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if camofox_eval(tab_id, "document.readyState") == "complete":
                return
        except Exception:
            pass
        time.sleep(0.5)


def camofox_eval(tab_id: str, expression: str) -> Any:
    r = _http.post(f"{CAMOFOX_URL}/tabs/{tab_id}/evaluate", json={
        "userId": CAMOFOX_USER, "expression": expression,
    })
    r.raise_for_status()
    data = r.json()
    if "result" in data:
        return data["result"]
    raise RuntimeError(f"camofox evaluate failed: {data}")


# ---------------------------------------------------------------- danbooru lookup

def _danbooru_autocomplete(tab_id: str, query: str) -> list[dict]:
    q = urllib.parse.quote(query)
    expr = (
        f"fetch('https://danbooru.donmai.us/autocomplete.json"
        f"?search%5Bquery%5D={q}&search%5Btype%5D=tag')"
        f".then(r=>r.json()).then(d=>JSON.stringify(d))"
    )
    raw = camofox_eval(tab_id, expr)
    return json.loads(raw) if isinstance(raw, str) else raw


def _danbooru_wiki(tab_id: str, canonical: str) -> dict:
    """Open the character's wiki page, extract body and page content."""
    camofox_navigate(tab_id, f"https://danbooru.donmai.us/wiki_pages/{canonical}")
    expr = ("(() => {"
            "  const body = document.querySelector('#wiki-page-body');"
            "  const content = document.querySelector('#content');"
            "  return JSON.stringify({"
            "    body: body ? body.innerText.slice(0, 6000) : '',"
            "    content: content ? content.innerText.slice(0, 6000) : ''"
            "  });"
            "})()")
    raw = camofox_eval(tab_id, expr)
    d = json.loads(raw) if isinstance(raw, str) else {}
    return {"body": d.get("body", ""), "content": d.get("content", "")}


def _parse_wiki(wiki: dict, canonical: str) -> dict:
    desc = re.sub(r"\n+", "\n", wiki["body"]).strip()
    for marker in ("Posts\nTerms", "See also", "External links"):
        if marker in desc:
            desc = desc.split(marker)[0].strip()
    content = wiki["content"]
    aliases = re.findall(r"aliased to this tag:\s*([a-z0-9_]+)", content)
    related = re.findall(r"implicate this tag:\s*([a-z0-9_()]+)", content)
    # localised names are the lines right after the title line, wherever it is
    names = []
    title = canonical.replace("_", " ")
    lines = content.splitlines()
    idx = next((i for i, ln in enumerate(lines) if ln.strip() == title), -1)
    if idx >= 0:
        for ln in lines[idx + 1:]:
            ln = ln.strip()
            if not ln or ln == "Default":
                break
            names.append(ln)
    return {"description": desc, "aliases": aliases, "implicates": related,
            "localized_names": names}


def _cache_slug(name: str) -> str:
    """Cache filename slug; keeps CJK so Chinese character names do not collide
    into a shared empty slug file. Falls back to a hash for pure-symbol input."""
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", name.lower()).strip("_")
    return slug or hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]


def lookup_character(character: str, force_refresh: bool = False) -> dict:
    """Look up a character on Danbooru via camofox browser (cached)."""
    cache_file = CACHE_DIR / f"{_cache_slug(character)}.json"
    if not force_refresh and cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < CACHE_TTL_DAYS * 86400:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            # consistency check: the cached entry must be for this character
            if cached.get("query", "").lower() == character.lower():
                return cached

    ensure_camofox()
    tab_id = camofox_tab()
    try:
        candidates = _danbooru_autocomplete(tab_id, character)
        # full name missed (typo / misremembered surname) -> retry with first word
        matched_query = character
        if not candidates and " " in character.strip():
            first_word = character.strip().split()[0]
            candidates = _danbooru_autocomplete(tab_id, first_word)
            if candidates:
                matched_query = first_word
        # tag category 4 == character; fall back to any category
        chars = [c for c in candidates if c.get("category") == 4] or candidates
        if not chars:
            raise RuntimeError(f"Danbooru: no tags found for '{character}'")
        best = chars[0]
        canonical = best["value"]
        result = {
            "query": character,
            "matched_query": matched_query,
            "canonical_tag": canonical,
            "label": best.get("label", ""),
            "post_count": best.get("post_count", 0),
            "category": best.get("category"),
            "candidates": [{"label": c.get("label"), "value": c.get("value"),
                            "post_count": c.get("post_count")} for c in chars[:8]],
        }
        result.update(_parse_wiki(_danbooru_wiki(tab_id, canonical), canonical))
        result["wiki_url"] = f"https://danbooru.donmai.us/wiki_pages/{canonical}"
    finally:
        camofox_close(tab_id)

    CACHE_DIR.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return result


# ---------------------------------------------------------------- pipeline

# 模型根目录：默认取 PIPELINE 同级的 models/，可用 MODELS_ROOT 环境变量指向自己 ComfyUI 的 models
MODELS_DIR = Path(os.environ.get("MODELS_ROOT", str(PIPELINE.parent / "models")))
# ComfyUI 输出目录：默认 MODELS_ROOT 上级的 output/（即 ComfyUI 根/output）
OUTPUT_DIR = Path(os.environ.get("COMFYUI_OUTPUT", str(MODELS_DIR.parent / "output")))
MCP_MARK = "good_comfyui_mcp"


def _load_pipeline() -> dict:
    if not PIPELINE.exists():
        raise FileNotFoundError(f"pipeline not found: {PIPELINE}")
    return json.loads(PIPELINE.read_text(encoding="utf-8"))


def _find_node(wf: dict, class_type: str, title_part: str | None = None) -> tuple[str, dict]:
    for nid, node in wf.items():
        if node["class_type"] != class_type:
            continue
        title = node.get("_meta", {}).get("title", "")
        if title_part is None or title_part in title:
            return nid, node
    raise RuntimeError(f"node not found: {class_type} ({title_part})")


def _check_resource(name: str, folder: str) -> None:
    """Raise if a model/lora file referenced by the submission is missing."""
    if not name:
        return
    candidates = [name]
    if folder == "loras" and "." not in name:
        candidates += [f"{name}.safetensors", f"{name}.sft"]
    if not any((MODELS_DIR / folder / c).exists() for c in candidates):
        raise RuntimeError(f"resource not found: models/{folder}/{name}")


def _validate_submission(wf: dict, unet_name: str, lora_text: str,
                         width: int | None, height: int | None) -> None:
    """Validate resources and parameters before submitting to ComfyUI."""
    _check_resource(unet_name, "diffusion_models")
    # LoRAs referenced in text (injected as a dynamic LoraLoader chain)
    lora_names = set(re.findall(r"<lora:([^:>]+)", lora_text))
    for name in lora_names:
        _check_resource(name, "loras")
    if width is not None and not (0 < width <= 4096):
        raise RuntimeError(f"invalid width: {width}")
    if height is not None and not (0 < height <= 4096):
        raise RuntimeError(f"invalid height: {height}")


def _find_cached_character(name: str) -> dict | None:
    """Return cached character info matching by canonical tag OR original query
    (so Chinese names hit the cache written under an English query)."""
    name_l = name.lower()
    if not CACHE_DIR.exists():
        return None
    for f in CACHE_DIR.glob("*.json"):
        if f.name.endswith(".appearance.json") or f.name.startswith("."):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if (d.get("canonical_tag", "").lower() == name_l
                or d.get("query", "").lower() == name_l):
            return d
    return None


def _character_from_prompt(prompt: str) -> str | None:
    """Extract the danbooru-style character tag (e.g. varesa_(genshin_impact))
    from a prompt, if present."""
    m = re.search(r"\b([a-z0-9_]+)\(([a-z0-9_ ]+)\)", prompt)
    return m.group(0).strip() if m else None


def _stamp_mcp_source(wf: dict) -> None:
    """Tag every node so history entries submitted by this MCP are identifiable."""
    for node in wf.values():
        node.setdefault("_meta", {})[MCP_MARK] = True


def _inject_lora_chain(wf: dict, lora_text: str) -> None:
    """动态注入 LoraLoader 链（标准内置节点，无 rgthree 依赖）。
    解析 <lora:name:strength>，在 UNETLoader 后插入串联节点，
    KSampler.model 与 CLIPTextEncode.clip 指向链尾；空 lora_text 时保持直连。"""
    parsed = [(mt.group(1), float(mt.group(2)))
              for mt in re.finditer(r"<lora:([^:>]+):([\d.]+)>", lora_text)]
    if not parsed:
        return
    _, unet = _find_node(wf, "UNETLoader")
    _, clip = _find_node(wf, "CLIPLoader")
    samplers = [n for n in wf.values() if n["class_type"] == "KSampler"]
    if not samplers:
        raise RuntimeError("pipeline has no KSampler node")
    used = {int(nid) for nid in wf if str(nid).isdigit()}
    nid = max(used) + 1
    prev_model, prev_clip, clip_id = "1", "6", "6"
    # 找到实际 loader 节点 id（避免硬编码 1/6）
    for k, v in wf.items():
        if v["class_type"] == "UNETLoader":
            prev_model = k
        elif v["class_type"] == "CLIPLoader":
            prev_clip = clip_id = k
    for name, strength in parsed:
        cur = str(nid); nid += 1
        # 首个 LoRA 的 clip 来自 CLIPLoader（单输出，索引 0）；后续来自上一个 LoraLoader 的 CLIP 输出（索引 1）
        clip_idx = 0 if prev_clip == clip_id else 1
        wf[cur] = {"class_type": "LoraLoader", "inputs": {
            "model": [prev_model, 0], "clip": [prev_clip, clip_idx],
            "lora_name": name, "strength_model": strength,
            "strength_clip": strength}}
        prev_model = prev_clip = cur
    # 重接采样与文本编码
    for s in samplers:
        s["inputs"]["model"] = [prev_model, 0]
    for _, n in wf.items():
        if n["class_type"] == "CLIPTextEncode":
            n["inputs"]["clip"] = [prev_clip, 1]


def run_pipeline(prompt: str, negative_prompt: str, seed: int | None = None,
                 width: int | None = None, height: int | None = None,
                 unet_name: str = "anima-base-v1.0.safetensors",
                 lora_text: str = "", scheduler: str = "simple",
                 timeout: int = 600) -> dict:
    """Run the default pipeline: inject prompt into the workflow, submit to
    ComfyUI, poll history, return output file paths. timeout covers queue wait
    plus generation; raise it when other jobs are ahead in the queue."""
    wf = _load_pipeline()
    _validate_submission(wf, unet_name, lora_text, width, height)

    _, pos = _find_node(wf, "CLIPTextEncode", "Positive")
    _, neg = _find_node(wf, "CLIPTextEncode", "Negative")
    pos["inputs"]["text"] = prompt
    neg["inputs"]["text"] = negative_prompt

    _, unet = _find_node(wf, "UNETLoader")
    if unet_name:
        unet["inputs"]["unet_name"] = unet_name

    samplers = [n for _, n in wf.items() if n["class_type"] == "KSampler"]
    if not samplers:
        raise RuntimeError("pipeline has no KSampler node")
    for s in samplers:
        s["inputs"]["scheduler"] = scheduler
    # main sampler gets the (random) seed; the hires-fix sampler deliberately
    # keeps its workflow-fixed seed for stable detail refinement
    samplers[0]["inputs"]["seed"] = seed if seed is not None else random.randrange(2**53)

    try:
        _, latent = _find_node(wf, "EmptyLatentImage")
        if width: latent["inputs"]["width"] = width
        if height: latent["inputs"]["height"] = height
    except RuntimeError:
        pass  # pipeline may define its own resolution

    # strip stale character LoRA so a new character doesn't inherit the old one
    _inject_lora_chain(wf, lora_text)

    _stamp_mcp_source(wf)
    r = _http.post(f"{COMFYUI_URL}/prompt",
                   json={"prompt": wf, "client_id": "good_comfyui_mcp"})
    if r.status_code != 200:
        raise RuntimeError(f"ComfyUI submit failed ({r.status_code}): {r.text[:500]}")
    prompt_id = r.json()["prompt_id"]

    deadline = time.time() + timeout
    start = time.time()
    while time.time() < deadline:
        time.sleep(2)
        h = _http.get(f"{COMFYUI_URL}/history/{prompt_id}")
        if h.status_code != 200:
            continue
        entry = h.json().get(prompt_id)
        if not entry:
            continue
        status = entry.get("status", {})
        if status.get("completed") or status.get("status_str") == "success":
            images = []
            for node_out in entry.get("outputs", {}).values():
                for img in node_out.get("images", []):
                    sub = img.get("subfolder", "")
                    images.append(str(Path(img["type"]) / sub / img["filename"]))
            return {"prompt_id": prompt_id, "status": "completed",
                    "outputs": images}
        if status.get("status_str") in ("error", "failed"):
            errs = [m.get("message", {}) for m in status.get("messages", [])
                    if m.get("type") == "execution_error"]
            if errs and isinstance(errs[0], dict):
                first = errs[0]
                raise RuntimeError(
                    f"ComfyUI execution error: {first.get('exception_message', errs)} "
                    f"(node={first.get('node_id', '?')}, "
                    f"type={first.get('exception_type', '?')})")
            raise RuntimeError(f"ComfyUI execution failed: {status}")
    raise TimeoutError(
        f"generation timed out after {int(time.time() - start)}s (prompt_id={prompt_id})")


KREA2_UNET = "DasiwaKrea2TurboRaw_cutedisasterV2Turbo.safetensors"
KREA2_OFFICIAL = "krea2_turbo_int8_convrot.safetensors"


def _build_krea2_workflow(prompt: str, negative_prompt: str, seed: int,
                          width: int, height: int, unet_name: str,
                          lora_list: list[dict] | None = None,
                          upscale: bool = False) -> dict:
    """Build a Krea2/Dasiwa text-to-image workflow: UNETLoader -> optional LoRA
    chain -> KSampler (8 steps CFG 1) -> optional RealESRGAN_x2plus upscale.
    lora_list entries: {"name": ..., "strength": ...} applied in order."""
    wf = {
        "1": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2"}},
        "2": {"class_type": "UNETLoader",
              "inputs": {"unet_name": unet_name, "weight_dtype": "default"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": prompt}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": negative_prompt}},
        "6": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": 8, "cfg": 1.0,
            "sampler_name": "er_sde", "scheduler": "simple", "denoise": 1.0,
            "model": ["2", 0], "positive": ["4", 0], "negative": ["5", 0],
            "latent_image": ["6", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
    }
    # LoRA chain: last lora feeds the KSampler model
    prev = "2"
    if lora_list:
        for i, lora in enumerate(lora_list):
            nid = str(20 + i)
            wf[nid] = {"class_type": "LoraLoader", "inputs": {
                "model": [prev, 0], "clip": ["1", 0],
                "lora_name": lora["name"],
                "strength_model": lora.get("strength", 1.0),
                "strength_clip": lora.get("strength", 1.0)}}
            prev = nid
    wf["7"]["inputs"]["model"] = [prev, 0]
    # optional 2x upscale with RealESRGAN_x2plus (matches reference workflows)
    img_src = "8"
    if upscale:
        wf["48"] = {"class_type": "UpscaleModelLoader",
                     "inputs": {"model_name": "RealESRGAN_x2plus.pth"}}
        wf["50"] = {"class_type": "ImageUpscaleWithModel",
                     "inputs": {"upscale_model": ["48", 0], "image": ["8", 0]}}
        img_src = "50"
    wf["9"] = {"class_type": "SaveImage",
                "inputs": {"images": [img_src, 0],
                            "filename_prefix": "Krea2/%year%-%month%-%day%/krea"}}
    return wf


def _submit_and_wait(wf: dict, timeout: int, tag: str = "good_comfyui_mcp") -> dict:
    """Submit a workflow to ComfyUI and poll history until done."""
    _stamp_mcp_source(wf)
    r = _http.post(f"{COMFYUI_URL}/prompt",
                   json={"prompt": wf, "client_id": tag})
    if r.status_code != 200:
        raise RuntimeError(f"ComfyUI submit failed ({r.status_code}): {r.text[:500]}")
    prompt_id = r.json()["prompt_id"]
    start = time.time()
    deadline = start + timeout
    while time.time() < deadline:
        time.sleep(2)
        h = _http.get(f"{COMFYUI_URL}/history/{prompt_id}")
        if h.status_code != 200:
            continue
        entry = h.json().get(prompt_id)
        if not entry:
            continue
        status = entry.get("status", {})
        if status.get("completed") or status.get("status_str") == "success":
            images = []
            for node_out in entry.get("outputs", {}).values():
                for img in node_out.get("images", []):
                    sub = img.get("subfolder", "")
                    images.append(str(Path(img["type"]) / sub / img["filename"]))
            return {"prompt_id": prompt_id, "status": "completed", "outputs": images}
        if status.get("status_str") in ("error", "failed"):
            errs = [m.get("message", {}) for m in status.get("messages", [])
                    if m.get("type") == "execution_error"]
            if errs and isinstance(errs[0], dict):
                first = errs[0]
                raise RuntimeError(
                    f"ComfyUI execution error: {first.get('exception_message', errs)} "
                    f"(node={first.get('node_id', '?')}, "
                    f"type={first.get('exception_type', '?')})")
            raise RuntimeError(f"ComfyUI execution failed: {status}")
    raise TimeoutError(
        f"generation timed out after {int(time.time() - start)}s (prompt_id={prompt_id})")


def run_krea2(prompt: str, negative_prompt: str = "", seed: int | None = None,
              width: int = 1024, height: int = 1536,
              unet_name: str = KREA2_UNET,
              lora_list: list[dict] | None = None,
              upscale: bool = False, timeout: int = 900) -> dict:
    """Run a Krea2/Dasiwa text-to-image job (8 steps CFG 1, er_sde/simple).
    lora_list entries: {"name": filename, "strength": float} applied in order.
    upscale=True adds RealESRGAN_x2plus 2x. Default unet is the Dasiwa anime
    finetune; official Krea2 int8 is krea2_turbo_int8_convrot.safetensors."""
    _check_resource(unet_name, "diffusion_models")
    for lora in lora_list or []:
        _check_resource(lora["name"], "loras")
    wf = _build_krea2_workflow(prompt, negative_prompt,
                               seed if seed is not None else random.randrange(2**53),
                               width, height, unet_name, lora_list, upscale)
    return _submit_and_wait(wf, timeout, "good_comfyui_mcp-krea2")


# ---------------------------------------------------------------- MCP tools

ANIMA_GUIDE = {
    "models": [
        {"name": "anima-base-v1.0", "desc": "预训练基础版：风格最中性、多样性最高。角色 LoRA 的训练和使用推荐底模。",
         "prompt": "默认风格朴素，必须用 quality tags 和 artist tags 才有表现"},
        {"name": "anima-aesthetic-v1.1", "desc": "美学微调版（base + 美学数据微调 + 内置风格/稳定化调整）：默认画风更精致。",
         "prompt": "提示词不需要 quality tags（可留 masterpiece, best quality）；不要用 score_* tags（会把画面推过头）；适合无 LoRA 直出"},
        {"name": "anima-turbo-v1.0", "desc": "蒸馏加速版：CFG 1、8-12 步出图，快且稳定，多样性略低",
         "prompt": "强默认风格，适合快速迭代"},
    ],
    "generation_settings": {
        "resolution": "512^2 ~ 1536^2 像素（工作流默认 1024x1536）",
        "steps_cfg": "30-50 步 / CFG 4-5（Turbo 版：CFG 1、8-12 步）",
        "samplers": ["er_sde（中性风格/平涂/锐线，官方默认）", "euler_a（更软更细的线）",
                     "dpmpp_2m_sde_gpu（更有创意但可能太野）", "euler（基本采样器，配 Turbo/Aesthetic 更稳）"],
        "scheduler_note": "官方未限定 scheduler；本环境实测 simple 调度器效果更好",
    },
    "prompting": {
        "style": "Danbooru 风格 tag + 自然语言可混用；tag 用英文小写、空格代替下划线（score_* 除外）；Gelbooru 版优先",
        "positive_prefix": "masterpiece, best quality, score_7, safe, ",
        "recommended_negative": "worst quality, low quality, score_1, score_2, score_3, artist name, blurry, jpeg artifacts, chromatic aberration",
        "tag_order": "[quality/meta/year/safety] [1girl/1boy] [角色] [系列] [@画师] [通用 tags]",
        "artist_tag": "画师标签必须加 @ 前缀（如 @big chungus），否则几乎无效",
        "weighting": "权重语法可用但要比 SDXL 更高，如 (chibi:2)",
        "quality_tags": ["人类评分：masterpiece/best quality/good/normal/low/worst",
                         "PonyV7 审美模型：score_9..score_1（可混用也可都不用）"],
        "safety_tags": ["safe", "sensitive", "nsfw", "explicit"],
        "time_tags": ["year 2025 / newest / recent / mid / early / old"],
        "natural_language": "角色名+系列名用标准英文大小写；纯自然语言至少 2 句；多角色时逐个描述外貌",
    },
    "lora_tips": {
        "train_base": "LoRA 用 base 版训练（官方原话）；aesthetic 版内置风格调整会干扰 LoRA",
        "use": "角色 LoRA 搭配 base 使用效果最干净；搭配 aesthetic 会串味",
        "hyperparams": "rank 32 起步 lr 2e-5；不要训练 LLM adapter（llm_adapter_lr=0）",
    },
    "limitations": ["不适合写实（动漫/插画特化）", "长文字渲染弱（单词可以，长句不行）",
                    "base 版默认风格很朴素，需要 quality/artist tags"],
    "license": "非商用许可（模型权重不能商用，生成的图片可商用）；基于 NVIDIA Cosmos-Predict2 的衍生模型",
}


@mcp.tool()
def get_model_guide() -> dict:
    """Official Anima model usage guide (from the circlestone-labs/Anima README):
    which model version to use, recommended samplers/steps/CFG, prompting rules
    (tag order, artist @ prefix, quality tags, aesthetic-version differences) and
    known limitations. Consult this when assembling a prompt or choosing
    generation parameters instead of guessing."""
    return ANIMA_GUIDE


def _describe_with_ollama(model: str, image_path: str, question: str,
                          num_ctx: int = 8192, num_gpu: int = 99) -> str:
    """Single question to an Ollama vision model (GPU by default)."""
    import base64
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    r = _http.post("http://127.0.0.1:11434/api/chat", json={
        "model": model,
        "messages": [{"role": "user", "content": question,
                      "images": [b64]}],
        "stream": False,
        "options": {"num_gpu": num_gpu, "num_ctx": num_ctx},
    }, timeout=900)
    r.raise_for_status()
    data = r.json()
    return data.get("message", {}).get("content", "") or data.get("error", "")


@mcp.tool()
def describe_image(image_path: str, question: str = "", detail: bool = False,
                   model: str = "") -> str:
    """Describe a local image with a local vision model (Ollama, GPU).
    Default model: qwen3-vl:8b (accurate, may refuse NSFW). When NSFW is
    refused, falls back to llava:7b (no filter, detailed). model= overrides.
    detail=True: 11 targeted questions in Chinese (hair gradient, eyes,
    body, clothing piece by piece, props, pose, scene, layered background,
    composition, art style, text/watermark) assembled into a full report.
    llava/moondream answer best in English; qwen3-vl answers Chinese."""
    main = model or "qwen3-vl:8b"
    nsfw_fallback = "llava:7b"
    if detail:
        questions = [
            "头发：发色（含渐变层次）、发型、长度、发饰配件（蝴蝶结/发夹/猫耳等）？",
            "眼睛：瞳色、眼型、表情细节（眼神、眉毛、嘴型）？",
            "体型与皮肤：体型特征、肤色、特殊标记（纹身/伤痕/痣）？",
            "服装：从上到下逐件描述（上衣/下装/袜子/鞋子）、材质、颜色、装饰、配饰（首饰/项圈/腰带）？",
            "手持物与道具：角色拿着或身边有什么道具？",
            "姿势：全身姿势细节（手/腿/头的位置和角度）、重心？",
            "场景：环境（室内/室外、具体场所、家具/建筑）、光线来源与方向、色调？",
            "背景分层：前景/中景/背景各有什么元素？虚化程度如何？",
            "构图：视角（俯视/仰视/平视）、景别（特写/近景/中景/全景）、人物在画面中的位置、留白情况？",
            "画风：具体风格（赛璐璐/厚涂/水彩/3D渲染）、线条特点、上色方式？",
            "画面文字/水印/特效：有没有文字、水印、光效、粒子、飘落物？",
        ]
        parts = []
        refused = False
        for i, q in enumerate(questions, 1):
            q = q + " 请详细列举，不要省略任何细节，分点回答。"
            ans = _describe_with_ollama(main, image_path, q).strip()
            if not ans or any(k in ans for k in ("无法提供", "不能", "抱歉", "不当内容", "公序良俗", "健康积极")):
                refused = True
                break
            parts.append(f"{i}. {q}\n   → {ans}")
        if refused:
            # NSFW: main model refuses -> fall back to llava:7b detail (EN)
            mq = [
                "Hair: exact hair color, hairstyle, length, any hair accessories?",
                "Eyes: eye color, eye shape, expression?",
                "Body: body type, skin details, what is she wearing exactly (top, bottom, shoes)?",
                "Pose: exact body position, what is she doing with her hands, legs, head?",
                "Objects: list every object visible in the image (furniture, props, food, toys)?",
                "Scene: indoor or outdoor, what room, background details, lighting?",
                "Art style: 2D anime, 3D, painterly, line art? Color palette?",
                "Camera angle: is the camera at eye level, looking up from below (low angle), or looking down from above (high angle)?",
                "Zoom level: is it a close-up, medium shot, waist-up, full body, or wide shot?",
                "Framing: where is the character positioned in the frame (center, left, right)? Is there much empty space around her?",
                "Perspective: is she seen from the front, side, three-quarter view, or from behind?",
            ]
            parts = []
            for i, q in enumerate(mq, 1):
                ans = _describe_with_ollama(nsfw_fallback, image_path, q,
                                            num_ctx=2048).strip()
                parts.append(f"{i}. {q}\n   → {ans}")
        return "\n\n".join(parts)
    if not question:
        question = ("请详细描述这张图片，用中文：1) 角色外貌（发型、发色、瞳色、体型）"
                    "2) 服装 3) 姿势/动作 4) 场景背景 5) 视角构图 6) 画风。")
    ans = _describe_with_ollama(main, image_path, question).strip()
    if not ans or any(k in ans for k in ("无法提供", "不能", "抱歉", "不当内容", "公序良俗")):
        ans = _describe_with_ollama(nsfw_fallback, image_path,
                                    "Describe this image in detail: character appearance, clothing, pose, background, art style.",
                                    num_ctx=2048).strip()
    return ans


@mcp.tool()
def setup_guide() -> list[dict]:
    """初始化引导清单：安装本 MCP 后需要完成的步骤（每步含操作/验证/是否必需）。
    配合 server_info 使用：先调 server_info 拿 missing 列表，再按本清单逐项
    引导用户完成初始化（缺哪步做哪步，做完重新调 server_info 验证）。"""
    return [
        {"step": 1, "title": "安装 Python 依赖",
         "action": "pip install -r requirements.txt（mcp/httpx/numpy/pillow）",
         "required": True, "verify": "python -c \"import mcp, httpx, numpy, PIL\""},
        {"step": 2, "title": "启动 ComfyUI",
         "action": "启动本地 ComfyUI（默认 http://127.0.0.1:8188）",
         "required": True, "verify": "server_info 返回 comfyui=online"},
        {"step": 3, "title": "放置管线模型",
         "action": "按 pipeline.json 引用放置：anima-base-v1.0（diffusion_models）、qwen_3_06b_base（text_encoders）、qwen_image_vae（vae）、RealESRGAN_x2plus（upscale_models）",
         "required": True, "verify": "server_info 返回 models_ok=true"},
        {"step": 4, "title": "确认管线模型就绪（无自定义节点依赖）",
         "action": "pipeline.json 只用 ComfyUI 内置节点（UNETLoader/CLIPLoader/VAELoader/LoraLoader/KSampler/RealESRGAN）",
         "required": True, "verify": "generate 能提交成功"},
        {"step": 5, "title": "拉取 Ollama 识图模型",
         "action": "ollama pull qwen3-vl:8b && ollama pull llava:7b",
         "required": True, "verify": "server_info 返回 ollama 两项 true"},
        {"step": 6, "title": "启动 camofox-browser",
         "action": "npm install -g camofox-browser && camofox-browser（默认 127.0.0.1:9377）",
         "required": True, "verify": "server_info 返回 camofox=online；缺了角色 tag/外貌查询不可用"},
        {"step": 7, "title": "配置 Civitai（可选）",
         "action": "设置 CIVITAI_TOKEN（下载）和 CIVITAI_SEARCH_KEY（搜索），获取方法见 README 5b",
         "required": False, "verify": "search_lora / download_lora 可用；不配不影响生成/识图"},
        {"step": 8, "title": "启动对比页服务器（可选）",
         "action": "cd 包目录 && python -m http.server 8899 -d compare",
         "required": False, "verify": "generate 返回的 view_url 可访问"},
        {"step": 9, "title": "跑内置样例验证",
         "action": "python run_example.py（内置 repro_anima_00015 / repro_sofa_rose 两个样例，含完整提示词/seed/LoRA）",
         "required": True, "verify": "输出与参考图 MAE<10（MAE≈0 = 环境完全一致）；差异大则检查模型/LoRA 是否齐全"},
    ]


@mcp.tool()
def server_info() -> dict:
    """依赖自检：ComfyUI、pipeline 引用的模型、自定义节点、Ollama 识图模型、
    camofox、Civitai 配置是否就绪。返回每项状态 + 缺失项的安装引导提示。
    建议每次会话开始时调用一次，按 missing[] 提示用户补齐依赖。"""
    import socket
    info = {"pipeline": str(PIPELINE), "pipeline_exists": PIPELINE.exists(),
            "missing": [], "ready": False}
    # ComfyUI
    try:
        r = _http.get(f"{COMFYUI_URL}/system_stats", timeout=5)
        info["comfyui"] = "online" if r.status_code == 200 else f"http {r.status_code}"
    except Exception as e:
        info["comfyui"] = f"offline ({e.__class__.__name__})"
    if info["comfyui"] != "online":
        info["missing"].append("ComfyUI 未运行（启动 ComfyUI，默认 127.0.0.1:8188）")
    # pipeline 引用的模型
    models_ok = True
    if PIPELINE.exists():
        try:
            wf = json.loads(PIPELINE.read_text(encoding="utf-8"))
            for nid, n in wf.items():
                ins = n.get("inputs", {})
                ct = n.get("class_type", "")
                if ct == "UNETLoader":
                    name = ins.get("unet_name", "")
                    p = MODELS_DIR / "diffusion_models" / name
                    if not p.exists():
                        models_ok = False
                        info["missing"].append(f"模型缺失: {p.name}（放 models/diffusion_models/）")
                elif ct == "CLIPLoader":
                    name = ins.get("clip_name", "")
                    p = MODELS_DIR / "text_encoders" / name
                    if not p.exists():
                        models_ok = False
                        info["missing"].append(f"CLIP 缺失: {p.name}（放 models/text_encoders/）")
                elif ct == "VAELoader":
                    name = ins.get("vae_name", "")
                    p = MODELS_DIR / "vae" / name
                    if not p.exists():
                        models_ok = False
                        info["missing"].append(f"VAE 缺失: {p.name}（放 models/vae/）")
                elif ct == "UpscaleModelLoader":
                    name = ins.get("model_name", "")
                    p = MODELS_DIR / "upscale_models" / name
                    if not p.exists():
                        models_ok = False
                        info["missing"].append(f"放大模型缺失: {p.name}（放 models/upscale_models/）")
        except Exception:
            models_ok = False
            info["missing"].append("pipeline.json 解析失败")
    info["models_ok"] = models_ok
    # Ollama 识图模型
    ollama = {}
    try:
        d = _http.get("http://127.0.0.1:11434/api/tags", timeout=5).json()
        have = {m["name"] for m in d.get("models", [])}
        for need in ("qwen3-vl:8b", "llava:7b"):
            ollama[need] = need in have
            if need not in have:
                info["missing"].append(f"Ollama 模型缺失: ollama pull {need}")
    except Exception as e:
        ollama = f"offline ({e.__class__.__name__})"
        info["missing"].append("Ollama 未运行（识图不可用；安装 Ollama 后 ollama pull qwen3-vl:8b llava:7b）")
    info["ollama"] = ollama
    # camofox
    try:
        info["camofox"] = ensure_camofox()
    except Exception as e:
        info["camofox"] = f"offline ({e})"
        info["missing"].append("camofox-browser 未运行（必需：角色 tag 查询不可用；npm install -g camofox-browser 后启动，README 第 4 步）")
    # Civitai 配置
    tok = os.environ.get("CIVITAI_TOKEN", "")
    key = os.environ.get("CIVITAI_SEARCH_KEY", "")
    info["civitai"] = {"token": bool(tok), "search_key": bool(key)}
    if not tok:
        info["missing"].append("CIVITAI_TOKEN 未配置（download_lora 不可用；README 5b 可选）")
    if not key:
        info["missing"].append("CIVITAI_SEARCH_KEY 未配置（search_lora 不可用；README 5b 可选）")
    info["ready"] = info["comfyui"] == "online" and models_ok
    info["recent_runs"] = _recent_run_sources()
    return info


def _recent_run_sources(limit: int = 5) -> list[dict]:
    """Classify recent ComfyUI runs as mcp or manual, from the _meta marker."""
    try:
        d = _http.get(f"{COMFYUI_URL}/history?max_items={limit}", timeout=5).json()
    except Exception:
        return []
    out = []
    for pid in d:
        entry = d[pid]
        wf = entry.get("prompt")
        if isinstance(wf, list) and len(wf) > 2:
            wf = wf[2]
        stamped = any(
            isinstance(n.get("_meta"), dict) and n["_meta"].get(MCP_MARK)
            for n in (wf or {}).values()
        ) if isinstance(wf, dict) else False
        outs = [img.get("filename", "") for o in entry.get("outputs", {}).values()
                for img in o.get("images", [])]
        out.append({"source": "mcp" if stamped else "manual",
                    "outputs": outs[:2], "prompt_id": pid[:12]})
    return out


@mcp.tool()
def lookup_character_tags(character: str, force_refresh: bool = False) -> dict:
    """Look up a character's Danbooru tags via camofox browser (cached per
    character for 30 days). Returns the canonical tag (e.g. your_character_tag_(your_series)),
    aliases, post count and the wiki description to use in prompts.
    Call this the first time a character is used, then assemble the prompt and
    CONFIRM IT WITH THE USER before calling generate()."""
    return lookup_character(character, force_refresh)


def _make_view(result: dict, reference_image: str | None = None) -> dict:
    """把输出图复制到 compare 目录并附加可访问 URL；有参考图时生成对比页（能对比就对比）。"""
    try:
        outs = result.get("outputs") or []
        if not outs:
            return result
        # outputs 可能带 "output/" 前缀（ComfyUI 相对根目录路径），也可能是相对 output/ 的路径
        p0 = str(outs[0]).replace("\\", "/")
        if p0.startswith("output/"):
            src = OUTPUT_DIR.parent / p0
        else:
            src = OUTPUT_DIR / p0
        if not src.exists():
            return result
        COMPARE_DIR.mkdir(exist_ok=True)
        stem = re.sub(r"[^\w\-]", "_", src.stem)[:40]
        dst = COMPARE_DIR / f"{stem}.png"
        shutil.copyfile(src, dst)
        if reference_image and Path(reference_image).exists():
            ref_dst = COMPARE_DIR / f"{stem}_ref.png"
            shutil.copyfile(reference_image, ref_dst)
            html = COMPARE_DIR / f"{stem}.html"
            html.write_text(
                "<!DOCTYPE html><html><head><meta charset='UTF-8'><title>对比</title>"
                "<style>body{margin:0;background:#1a1a2e;color:#eee;font-family:sans-serif;"
                "padding:15px}h1{text-align:center;font-size:18px}.container{display:flex;"
                "gap:12px;justify-content:center;flex-wrap:wrap}.card{text-align:center}"
                ".card h2{font-size:14px}.card img{height:80vh;max-width:46vw;object-fit:contain;"
                "border:2px solid #444;border-radius:8px;background:#222}</style></head><body>"
                f"<h1>原图 vs 复刻</h1><div class='container'>"
                f"<div class='card'><h2>\U0001F5BC 原图</h2><img src='{ref_dst.name}'></div>"
                f"<div class='card'><h2>\U0001F3A8 生成</h2><img src='{dst.name}'></div>"
                "</div></body></html>", encoding="utf-8")
            result["view_url"] = f"{VIEW_BASE}/{html.name}"
        else:
            result["view_url"] = f"{VIEW_BASE}/{dst.name}"
    except Exception:
        pass
    return result


@mcp.tool()
def generate(prompt: str, negative_prompt: str = "", seed: int | None = None,
             width: int | None = None, height: int | None = None,
             unet_name: str = "anima-base-v1.0.safetensors",
             lora_text: str | None = None, scheduler: str = "simple",
             character: str | None = None, timeout: int = 600,
             engine: str = "anima",
             steps: int | None = None, cfg: float | None = None,
             sampler_name: str | None = None,
             lora_list: list[dict] | None = None,
             upscale: bool = False,
             reference_image: str | None = None) -> dict:
    """Generate an image on the local ComfyUI and wait for the result.
    IMPORTANT: only call after the prompt has been confirmed with the user.

    reference_image: optional path to a reference image (e.g. a QQ download /
    un-confused reference). When given, the result includes a side-by-side
    comparison page URL (view_url) instead of a plain image URL.

    PRESENTATION (重要): the result always includes `view_url` (points to the
    local 8899 static server serving the compare/ dir). When you have a
    browser tool (browser_open / screenshot), ALWAYS open view_url in the
    browser to SHOW the user the generated image (and comparison page when a
    reference was given). If the 8899 server is down (view_url unreachable),
    fall back to giving the user the output file path.

    engine="anima" (default): the pipeline.json pipeline (single KSampler,
      30 steps cfg 4.0 euler_ancestral simple + RealESRGAN_x2plus upscale,
      no hires fix, built-in nodes only).
    engine="krea2": Krea2/Dasiwa natural-language model, 8 steps CFG 1
      er_sde/simple, 1024x1536 default. unet_name defaults to the Dasiwa
      finetune for this engine. lora_list: [{"name": file, "strength": x}].
      upscale=True adds RealESRGAN_x2plus 2x.

    negative_prompt defaults to a quality blocklist; character auto-check: if
    `character` is given (or a danbooru-style tag is found in the prompt) and
    not looked up before, it is looked up on Danbooru and returned alongside
    the result for the agent to verify with the user."""
    if lora_text is None:
        lora_text = ", ".join(f"<lora:{n}:{w}>" for n, w in DEFAULT_LORAS)
    neg = negative_prompt or ("(score_4, score_5, score_6:1.2), worst quality, low quality, "
                              "normal quality, bad hands, bad feet, bad anatomy, bad "
                              "proportions, cropped, missing fingers, jpeg artifacts, "
                              "signature, watermark, username, artist name, extra digit, "
                              "fewer digits, artistic error")
    info = None
    char_tag = character or _character_from_prompt(prompt)
    if char_tag:
        if _find_cached_character(char_tag):
            info = {"character": char_tag, "looked_up": False}
        else:
            try:
                info = lookup_character(char_tag)
                info["looked_up"] = True
            except RuntimeError as e:
                info = {"character": char_tag, "looked_up": False,
                        "warning": f"auto lookup failed: {e}"}
    if engine == "krea2":
        if width is None:
            width = 1024
        if height is None:
            height = 1536
        if unet_name == "anima-base-v1.0.safetensors":
            unet_name = KREA2_UNET
        result = run_krea2(prompt, neg, seed, width, height, unet_name,
                           lora_list, upscale, timeout)
    else:
        wf = _load_pipeline()
        _validate_submission(wf, unet_name, lora_text, width, height)
        _, pos = _find_node(wf, "CLIPTextEncode", "Positive")
        _, neg_node = _find_node(wf, "CLIPTextEncode", "Negative")
        pos["inputs"]["text"] = prompt
        neg_node["inputs"]["text"] = neg
        _, unet = _find_node(wf, "UNETLoader")
        if unet_name:
            unet["inputs"]["unet_name"] = unet_name
        samplers = [n for _, n in wf.items() if n["class_type"] == "KSampler"]
        if not samplers:
            raise RuntimeError("pipeline has no KSampler node")
        for s in samplers:
            s["inputs"]["scheduler"] = scheduler
        main = samplers[0]
        main["inputs"]["seed"] = seed if seed is not None else random.randrange(2**53)
        if steps:
            main["inputs"]["steps"] = steps
        if cfg:
            main["inputs"]["cfg"] = cfg
        if sampler_name:
            main["inputs"]["sampler_name"] = sampler_name
        try:
            _, latent = _find_node(wf, "EmptyLatentImage")
            if width:
                latent["inputs"]["width"] = width
            if height:
                latent["inputs"]["height"] = height
        except RuntimeError:
            pass
        _inject_lora_chain(wf, lora_text)
        result = _submit_and_wait(wf, timeout)
    if info:
        result["character_info"] = info
    return _make_view(result, reference_image)


@mcp.tool()
def extract_image_info(image_path: str) -> dict:
    """Parse an image file (PNG/JPEG) for embedded generation metadata:
    ComfyUI (prompt/workflow JSON: model, seed, sampler, LoRAs, prompt text),
    WebUI/NovelAI (parameters: prompt + settings). Returns whatever is found;
    QQ-forwarded images usually have none (only pixels)."""
    from PIL import Image
    path = Path(image_path)
    info = {"file": str(path), "size": None, "metadata": {}}
    try:
        img = Image.open(path)
        info["size"] = list(img.size)
    except Exception as e:
        info["error"] = f"cannot open image: {e}"
        return info
    data = path.read_bytes()
    if path.suffix.lower() == ".png":
        pos = 8
        while pos < len(data):
            if pos + 8 > len(data):
                break
            length = struct.unpack(">I", data[pos:pos + 4])[0]
            ctype = data[pos + 4:pos + 8].decode("latin1")
            chunk = data[pos + 8:pos + 8 + length]
            if ctype in ("tEXt", "iTXt", "zTXt"):
                try:
                    txt = chunk.decode("utf-8", "replace")
                    key = txt.split("\x00", 1)[0] if "\x00" in txt else ""
                    val = txt.split("\x00", 1)[1] if "\x00" in txt else txt
                    if key in ("parameters", "prompt", "workflow", "Comment", "Description"):
                        # keep prompt/workflow whole so they can be parsed; trim the rest
                        info["metadata"][key] = val if key in ("prompt", "workflow") else val[:2000]
                except Exception:
                    pass
            pos += 12 + length
    else:
        exif = img.getexif()
        if exif:
            info["metadata"]["exif_tags"] = len(exif)
    # parse key generation parameters out of ComfyUI prompt JSON
    meta = info["metadata"]
    if "prompt" in meta and meta["prompt"].lstrip().startswith("{"):
        try:
            pj = json.loads(meta["prompt"])
            parsed = {}
            for nid, n in pj.items():
                ct = n.get("class_type", "")
                ins = n.get("inputs", {})
                if ct == "KSampler":
                    parsed["sampler"] = {k: ins.get(k) for k in
                                          ("seed", "steps", "cfg", "sampler_name", "scheduler", "denoise")}
                elif ct == "UNETLoader":
                    parsed["model"] = ins.get("unet_name")
                elif ct == "EmptyLatentImage":
                    parsed["size"] = [ins.get("width"), ins.get("height")]
                elif ct == "CLIPTextEncode":
                    t = ins.get("text", "")
                    if len(t) > 80:
                        parsed.setdefault("prompt_text", t)
                elif "Lora" in ct or "lora" in ct:
                    # standard LoraLoader: lora_name widget
                    name = ins.get("lora_name")
                    if name:
                        parsed.setdefault("loras", []).append({
                            "node": ct, "lora": name,
                            "strength": ins.get("strength_model") or ins.get("strength")})
                    # rgthree Power Lora Loader: inputs.loras = {__value__: [...]}
                    # or legacy per-slot fields: inputs.lora_1 = {on, lora, strength}
                    loras_in = ins.get("loras")
                    if isinstance(loras_in, dict):
                        for entry in loras_in.get("__value__", []) or []:
                            if isinstance(entry, dict) and entry.get("lora"):
                                parsed.setdefault("loras", []).append({
                                    "node": ct, "lora": entry["lora"],
                                    "strength": entry.get("strength"),
                                    "enabled": bool(entry.get("on"))})
                    for k, entry in ins.items():
                        if k.startswith("lora_") and isinstance(entry, dict) and entry.get("lora"):
                            parsed.setdefault("loras", []).append({
                                "node": ct, "lora": entry["lora"],
                                "strength": entry.get("strength"),
                                "enabled": bool(entry.get("on"))})
                    # ZML Power Lora Loader: lora_loader_data JSON with entries[]
                    zml = ins.get("lora_loader_data")
                    if isinstance(zml, str) and zml.lstrip().startswith("{"):
                        try:
                            for entry in json.loads(zml).get("entries", []):
                                if entry.get("item_type") == "lora" and entry.get("lora_name"):
                                    parsed.setdefault("loras", []).append({
                                        "node": ct, "lora": entry["lora_name"],
                                        "strength": entry.get("weight"),
                                        "enabled": bool(entry.get("enabled"))})
                        except Exception:
                            pass
            info["generation"] = parsed
        except Exception:
            pass
    # workflow 里的 LoRA 配置（ZML 的 widgets JSON / rgthree 的 per-slot 字段等，prompt 里可能缺失）
    wf_meta = meta.get("workflow")
    if wf_meta and wf_meta.lstrip().startswith("{"):
        try:
            wj = json.loads(wf_meta)
            wf_loras = []
            for n in wj.get("nodes", []):
                t = n.get("type", "")
                if "lora" not in t.lower():
                    continue
                wv = n.get("widgets_values")
                if not wv:
                    continue
                if isinstance(wv[0], str) and wv[0].lstrip().startswith("{"):
                    try:
                        for entry in json.loads(wv[0]).get("entries", []):
                            if entry.get("item_type") == "lora" and entry.get("lora_name"):
                                wf_loras.append({
                                    "node": t, "lora": entry["lora_name"],
                                    "strength": entry.get("weight"),
                                    "enabled": bool(entry.get("enabled"))})
                    except Exception:
                        pass
                elif isinstance(wv[0], dict):
                    # rgthree UI 格式的 widgets 数组
                    for entry in wv[0].get("__value__", []) or []:
                        if isinstance(entry, dict) and entry.get("lora"):
                            wf_loras.append({"node": t, "lora": entry["lora"],
                                             "strength": entry.get("strength"),
                                             "enabled": bool(entry.get("on"))})
            if wf_loras:
                gen = info.setdefault("generation", {})
                gen.setdefault("loras", []).extend(
                    l for l in wf_loras if l not in gen.get("loras", []))
            # workflow 里的模型/采样参数（prompt 缺失时补充）
            if "model" not in gen:
                for n in wj.get("nodes", []):
                    if n.get("type") == "UNETLoader" and n.get("widgets_values"):
                        gen["model"] = n["widgets_values"][0]
                        break
            if "sampler" not in gen:
                for n in wj.get("nodes", []):
                    t = n.get("type")
                    if t in ("KSampler", "KSamplerAdvanced") and n.get("widgets_values"):
                        wv = n["widgets_values"]
                        if t == "KSampler":
                            gen["sampler"] = {"seed": wv[0], "steps": wv[2], "cfg": wv[3],
                                               "sampler_name": wv[4], "scheduler": wv[5], "denoise": wv[6]}
                        else:
                            gen["sampler"] = {"seed": wv[1], "steps": wv[3], "cfg": wv[4],
                                               "sampler_name": wv[5], "scheduler": wv[6]}
                        break
        except Exception:
            pass
    if "parameters" in meta:
        gen = info.setdefault("generation", {})
        gen.setdefault("parameters_head", meta["parameters"][:600])
    return info


@mcp.tool()
def lookup_character_appearance(character: str, sample: int = 50) -> dict:
    """Statistically determine a character's real appearance tags: fetches the
    most recent solo posts of the character on Danbooru (via camofox) and
    counts which tags co-occur most often (hair colour, eyes, clothing, traits).
    Use the top tags when building the prompt instead of guessing.
    Results are cached like lookup_character_tags."""
    info = lookup_character_tags(character)
    canonical = info["canonical_tag"]
    slug = re.sub(r"[^a-z0-9]+", "_", canonical).strip("_")
    cache_file = CACHE_DIR / f"{slug}.appearance.json"
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < CACHE_TTL_DAYS * 86400:
            return json.loads(cache_file.read_text(encoding="utf-8"))
    ensure_camofox()
    tab_id = camofox_tab()
    try:
        import urllib.parse as _up
        tags = _up.quote(f"{canonical} solo")
        expr = (f"fetch('https://danbooru.donmai.us/posts.json?tags={tags}&limit={sample}')"
                f".then(r=>r.json()).then(d=>JSON.stringify(d))")
        raw = camofox_eval(tab_id, expr)
        posts = json.loads(raw) if isinstance(raw, str) else raw
    finally:
        camofox_close(tab_id)
    if not isinstance(posts, list) or not posts:
        raise RuntimeError(f"no solo posts found for {canonical}")
    counts = {}
    for p in posts:
        for t in p.get("tag_string", "").split():
            counts[t] = counts.get(t, 0) + 1
    skip = {canonical, "solo", "1girl", "1boy", "genshin_impact", "absurdres",
            "highres", "commentary", "commentary_request", "translated",
            "multiple_girls", "multi_girl"}
    ranked = sorted(((n, t) for t, n in counts.items() if t not in skip),
                    reverse=True)
    result = {"canonical_tag": canonical, "sample_size": len(posts),
              "appearance_tags": [t for n, t in ranked if n >= sample * 0.3],
              "top_tags": [{"tag": t, "count": n} for n, t in ranked[:25]]}
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return result


@mcp.tool()
def search_lora(filename: str, fresh: bool = False, base_model: str | None = None) -> dict:
    """穷举搜索 LoRA 精确版（civitai.red 网页搜索端点 models_v9，比 API 搜索全——
    API 搜不到的模型如 surtr945 2692601、Hentai Studio Quality 1459030 这里能搜到）。
    自动尝试：完整文件名（保留 @/_/---）→ 文件名一字不差匹配 → trainedWords 触发词匹配
    （前缀/相等，短词防误报）→ 指定 base 优先（如 "Anima"，从工作流 UNETLoader 提取）→ 有文件优先。
    返回 {"exact", "kind"(KNOWN/EXACT/EXACT-TRIGGER), "model_id", "version_id",
    "author", "base", "trained_words", "candidates"}。
    fresh=True 跳过 KNOWN_EXACT 已知表重新搜索。下载用
    https://civitai.red/api/download/models/<version_id>?token=<密钥>"""
    import lora_search as _ls
    return _ls.find_exact_data(filename, fresh=fresh, base_model=base_model)


@mcp.tool()
def deconfuse_image(image_path: str, times: int = 1,
                    out_path: str | None = None) -> dict:
    """小番茄混淆解混淆（Gilbert 曲线像素重排，可逆）。
    输入混淆图，输出还原图。检测线索：相邻像素相关性高（<15）但视觉是碎片。
    注意：JPEG 压缩/缩放过的混淆图可能无法还原（曲线位置失配）。
    times: 混淆次数（多次混淆需相同次数反向操作）。"""
    src = Path(image_path)
    if not src.exists():
        raise RuntimeError(f"file not found: {image_path}")
    out = Path(out_path) if out_path else src.with_name(src.stem + "_dec.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    import subprocess as _sp
    r = _sp.run([sys.executable, str(Path(__file__).parent / "xfq_tool.py"),
                 str(src), "--mode", "dec", "--times", str(times),
                 "--out", str(out)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"deconfuse failed: {r.stderr[:300]}")
    return {"input": str(src), "output": str(out), "times": times,
            "log": r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""}


@mcp.tool()
def download_lora(version_id: int, filename: str,
                  subdir: str = "") -> dict:
    """Download a LoRA from civitai.red and verify it is a valid safetensors.
    version_id: C 站 model version id（search_lora 返回的 version_id）。
    filename: 保存的文件名（含 .safetensors）。subdir: 可选子目录（如 krea2/style）。
    验证：文件头可解析为 safetensors（keys>0）且大小 >100KB（非 HTML 错误页）。"""
    import struct as _st
    dest = MODELS_DIR / "loras"
    if subdir:
        dest = dest / subdir
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / filename
    url = f"https://civitai.red/api/download/models/{version_id}"
    r = _http.get(url, params={"token": os.environ.get("CIVITAI_TOKEN", "")},
                  timeout=600, follow_redirects=True)
    if r.status_code != 200 or len(r.content) < 100_000:
        raise RuntimeError(
            f"download failed http={r.status_code} size={len(r.content)}")
    target.write_bytes(r.content)
    # 验证 safetensors 头
    try:
        with target.open("rb") as fp:
            n = _st.unpack("<Q", fp.read(8))[0]
            header = json.loads(fp.read(n))
        keys = len(header.get("__metadata__", {})) if header.get("__metadata__") else len(header)
        ok = n > 0 and n < len(r.content)
    except Exception:
        ok = False
        keys = 0
    return {"saved": str(target), "size_mb": round(len(r.content) / 1048576, 1),
            "valid_safetensors": ok, "metadata_keys": keys}


@mcp.tool()
def lookup_lora_hash(local_path: str) -> dict:
    """算本地 LoRA 文件 SHA256，用 C 站 /model-versions/by-hash 反查精确来源
    （比穷举搜索可靠——搜索索引漏掉的模型 by-hash 也能查到）。
    返回 modelId/verId/baseModel/文件名/状态。"""
    import hashlib as _hl
    p = Path(local_path)
    if not p.exists():
        raise RuntimeError(f"file not found: {local_path}")
    h = _hl.sha256()
    with p.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    sha = h.hexdigest().upper()
    # by-hash 是公开端点（免 token）；无 token 时不带 Authorization 头（空 Bearer 会报协议错误）
    headers = {}
    tok = os.environ.get('CIVITAI_TOKEN', '')
    if tok:
        headers['Authorization'] = f'Bearer {tok}'
    r = _http.get(f"https://civitai.red/api/v1/model-versions/by-hash/{sha}",
                   headers=headers,
                   timeout=60, follow_redirects=True)
    if r.status_code != 200:
        return {"sha256": sha, "hit": False}
    d = r.json()
    m = d.get("model") or {}
    files = [f["name"] for f in d.get("files", []) if f.get("type") == "Model"]
    return {"sha256": sha, "hit": True, "model_id": d.get("modelId"),
            "version_id": d.get("id"), "base_model": d.get("baseModel"),
            "status": d.get("status"), "model_name": m.get("name"),
            "files": files}


@mcp.tool()
def list_cached_characters() -> list[dict]:
    """List characters already looked up (cached tag data, no network)."""
    out = []
    for f in sorted(CACHE_DIR.glob("*.json")):
        if f.name.endswith(".appearance.json") or f.name.startswith("."):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not d.get("query") or not d.get("canonical_tag"):
            continue
        out.append({"query": d["query"], "canonical_tag": d["canonical_tag"],
                    "post_count": d.get("post_count")})
    return out


def main():
    mcp.run()


if __name__ == "__main__":
    main()
