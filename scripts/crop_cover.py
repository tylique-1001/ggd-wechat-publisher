#!/usr/bin/env python3
"""封面图处理：从正方形图片中心裁剪为 900x383 (2.35:1)，不变形不拉伸"""
import sys
from PIL import Image

def crop_cover(input_path, output_path, target_w=900, target_h=383):
    img = Image.open(input_path)
    orig_w, orig_h = img.size
    
    # 计算裁剪区域：保持原始宽高比，从中心切出目标比例的区域
    target_ratio = target_w / target_h  # ~2.35
    orig_ratio = orig_w / orig_h
    
    if orig_ratio > target_ratio:
        # 原图更"宽"，上下裁
        new_w = int(orig_h * target_ratio)
        left = (orig_w - new_w) // 2
        box = (left, 0, left + new_w, orig_h)
    else:
        # 原图更"高"或正方形，左右裁
        new_h = int(orig_w / target_ratio)
        top = (orig_h - new_h) // 2
        box = (0, top, orig_w, top + new_h)
    
    cropped = img.crop(box)
    resized = cropped.resize((target_w, target_h), Image.LANCZOS)
    resized.save(output_path, 'PNG')
    
    print(f"{input_path} -> {output_path}")
    print(f"  原始: {orig_w}x{orig_h}, 裁剪区: {box}, 输出: {target_w}x{target_h}")
    return output_path

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: crop_cover.py <input> <output>")
        sys.exit(1)
    crop_cover(sys.argv[1], sys.argv[2])
