"""小番茄混淆（Gilbert Curve）图片解混淆/混淆工具
============================================
来源：xiaofanqiehunxiao.com 前端算法（纯前端、无密钥、可逆）

用法：
    python xfq_tool.py <图片路径> [--mode dec|enc] [--times N] [--out 输出路径]

    --mode dec   解混淆（默认，还原被小番茄混淆的图）
    --mode enc   混淆（正向加密）
    --times N    操作次数（默认 1；若原图被多次混淆需多次解）
    --out 路径   输出文件路径（默认 <原名>_decoded.png）

原理：
    小番茄混淆基于 Gilbert 空间填充曲线做像素置换：
      1. 生成 w×h 的 Gilbert 曲线坐标序列 u
      2. 黄金比例偏移 L = round((√5-1)/2 * 像素总数)
      3. 置换：像素[s] <-> 像素[(s+L)%d]
    enc 正向、dec 反向，两者互逆，支持多次叠加。
    （JPEG 压缩后仍可还原，因为曲线保留像素邻域相关性）
"""
import argparse
import io
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


# ---------------------------------------------------------------- 算法复刻
def gilbert_curve(w: int, h: int) -> list:
    """Gilbert 空间填充曲线坐标序列。返回 [[x,y], ...]，长度 w*h。"""
    def p(t, n, e, o, c, a, r):
        m = abs(e + o)
        l = abs(c + a)
        u = 1 if e > 0 else (-1 if e < 0 else 0)
        d = 1 if o > 0 else (-1 if o < 0 else 0)
        L = 1 if c > 0 else (-1 if c < 0 else 0)
        s = 1 if a > 0 else (-1 if a < 0 else 0)
        if l == 1:
            for _ in range(m):
                r.append([t, n]); t += u; n += d
            return
        if m == 1:
            for _ in range(l):
                r.append([t, n]); t += L; n += s
            return
        h = math.floor(e / 2)
        g = math.floor(o / 2)
        i = math.floor(c / 2)
        f = math.floor(a / 2)
        S = abs(h + g)
        _ = abs(i + f)
        if 2 * m > 3 * l:
            if S % 2 and m > 2:
                h += u; g += d
            p(t, n, h, g, c, a, r)
            p(t + h, n + g, e - h, o - g, c, a, r)
        else:
            if _ % 2 and l > 2:
                i += L; f += s
            p(t, n, i, f, h, g, r)
            p(t + i, n + f, e, o, c - i, a - f, r)
            p(t + (e - u) + (i - L), n + (o - d) + (f - s),
              -i, -f, -(e - h), -(o - g), r)

    r = []
    if w >= h:
        p(0, 0, w, 0, 0, h, r)
    else:
        p(0, 0, 0, h, w, 0, r)
    return r


def xfq_transform(img_arr: np.ndarray, mode: str = 'dec') -> np.ndarray:
    """混淆(enc) / 解混淆(dec) 一次。mode: 'enc' | 'dec'"""
    c, a = img_arr.shape[1], img_arr.shape[0]
    u = gilbert_curve(c, a)
    d = c * a
    L = round((math.sqrt(5) - 1) / 2 * d)
    out = np.zeros_like(img_arr)
    flat_in = img_arr.reshape(-1, img_arr.shape[2])
    flat_out = out.reshape(-1, img_arr.shape[2])
    for s in range(d):
        hx, hy = u[s]
        gx, gy = u[(s + L) % d]
        si = hx + hy * c
        sf = gx + gy * c
        if mode == 'enc':
            flat_out[sf] = flat_in[si]
        else:
            flat_out[si] = flat_in[sf]
    return out


def main():
    ap = argparse.ArgumentParser(description='小番茄混淆（Gilbert Curve）图片混淆/解混淆')
    ap.add_argument('image', help='输入图片路径')
    ap.add_argument('--mode', choices=['dec', 'enc'], default='dec', help='dec=解混淆（默认） enc=混淆')
    ap.add_argument('--times', type=int, default=1, help='操作次数（默认 1）')
    ap.add_argument('--out', default=None, help='输出路径（默认 <原名>_decoded/encrypted.png）')
    ap.add_argument('--preserve-meta', action='store_true',
                    help='混淆时保留 PNG 文本元数据（tEXt/iTXt/zTXt），解混淆后可找回原提示词')
    args = ap.parse_args()

    src = Path(args.image)
    img = Image.open(src).convert('RGB')
    arr = np.array(img)
    print(f'输入: {src} ({arr.shape[1]}x{arr.shape[0]})')

    for i in range(args.times):
        arr = xfq_transform(arr, args.mode)
        print(f'  第{i+1}次{("解混淆" if args.mode=="dec" else "混淆")}完成')

    suffix = 'decoded' if args.mode == 'dec' else 'encrypted'
    out = Path(args.out) if args.out else src.with_name(f'{src.stem}_{suffix}.png')

    if args.preserve_meta and args.mode == 'enc':
        text_chunks, _ = _parse_chunks(src.read_bytes())
        if text_chunks:
            _write_png_with_meta(arr, out, text_chunks)
            print(f'输出: {out}（元数据已保留）')
            return
        print('警告: 输入无文本元数据，直接保存')

    if args.preserve_meta and args.mode == 'dec':
        # 解混淆也保留输入里的元数据（混淆时若用了 --preserve-meta，解回后提示词仍在）
        text_chunks, _ = _parse_chunks(src.read_bytes())
        if text_chunks:
            _write_png_with_meta(arr, out, text_chunks)
            print(f'输出: {out}（元数据已保留）')
            return

    Image.fromarray(arr).save(out)
    print(f'输出: {out}')


def _parse_chunks(data: bytes):
    """解析 PNG chunk，返回 (文本chunk列表, IDAT合并数据)。"""
    import struct as _s
    pos = 8
    text_chunks = []
    idat = b''
    while pos < len(data):
        length = _s.unpack('>I', data[pos:pos+4])[0]
        ctype = data[pos+4:pos+8].decode('latin1')
        cd = data[pos+8:pos+8+length]
        if ctype in ('tEXt', 'iTXt', 'zTXt'):
            text_chunks.append((ctype, cd))
        elif ctype == 'IDAT':
            idat += cd
        pos += 12 + length
    return text_chunks, idat


def _write_png_with_meta(arr: np.ndarray, out: Path, text_chunks: list):
    """写 PNG：像素 arr -> 新 IDAT，原文本 chunk 原样保留。"""
    import io as _io
    import struct as _s
    import zlib as _z
    img = Image.fromarray(arr)
    buf = _io.BytesIO()
    img.save(buf, 'PNG')
    new = buf.getvalue()
    pos = 8
    new_chunks = []
    fresh_idat = b''
    while pos < len(new):
        length = _s.unpack('>I', new[pos:pos+4])[0]
        ctype = new[pos+4:pos+8].decode('latin1')
        cd = new[pos+8:pos+8+length]
        if ctype == 'IDAT':
            fresh_idat += cd
        else:
            new_chunks.append((ctype, cd))
        pos += 12 + length
    out_b = bytearray(b'\x89PNG\r\n\x1a\n')
    for ctype, cd in new_chunks:
        if ctype in ('IDAT', 'IEND'):
            continue
        out_b += _s.pack('>I', len(cd)) + ctype.encode() + cd
        out_b += _s.pack('>I', _z.crc32(ctype.encode() + cd) & 0xffffffff)
    for ctype, cd in text_chunks:
        out_b += _s.pack('>I', len(cd)) + ctype.encode() + cd
        out_b += _s.pack('>I', _z.crc32(ctype.encode() + cd) & 0xffffffff)
    out_b += _s.pack('>I', len(fresh_idat)) + b'IDAT' + fresh_idat
    out_b += _s.pack('>I', _z.crc32(b'IDAT' + fresh_idat) & 0xffffffff)
    out_b += _s.pack('>I', 0) + b'IEND'
    out_b += _s.pack('>I', _z.crc32(b'IEND') & 0xffffffff)
    out.write_bytes(bytes(out_b))


if __name__ == '__main__':
    main()
