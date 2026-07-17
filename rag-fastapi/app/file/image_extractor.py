"""财报 PDF 图像处理模块

第一性原理
----------
1. 装饰 logo（面积 <10000 px²，即 <100×100）→ 直接跳过，噪声
2. 大图（≥4M 像素）→ 降采样到 ≤4M 再 OCR，避免 OOM
3. 纵向图（h > 1.5w）→ 旋转 90° 再 OCR（拍照表格常竖着）
4. 图片主导页（图占页面 ≥50% + 文本 <200 字符）→ 整页栅格化 OCR，
   替代原来仅 <50 字才走 OCR 的兜底（会漏"20% 字 + 80% 图"页）
5. 嵌入普通图（尺寸达标 + 不是上面 1-4）→ 独立 OCR，文本追加到该页正文

对外接口
--------
- classify_image(w, h) -> Literal["skip", "large", "vertical", "normal"]
- prepare_image_for_ocr(pixmap) -> bytes  # 处理降采样/旋转，返回 PNG bytes
- is_image_dominant_page(page, text_len) -> bool
- extract_embedded_images(page) -> list[bytes]  # 页内所有值得 OCR 的嵌入图

不做的事
--------
- 不做数据图表识别（VLM 才能做，KL）
- 不做图片形式的表识别（VLM，KL）
- 不做侧边栏图片识别（规则不可靠，KL）
"""

from __future__ import annotations

import io
from typing import List, Literal, Optional, Tuple

# ------------------------------------------------------------------
# 阈值（第一性原理已在 docstring 说明）
# ------------------------------------------------------------------
LOGO_MAX_AREA = 100 * 100          # 100×100 以下判装饰
LOGO_MIN_SIDE = 100                # 或任一边 <100
LARGE_IMAGE_PIXELS = 4_000_000     # 4M 像素以上判大图
VERTICAL_RATIO = 1.5               # h/w > 1.5 判纵向
IMAGE_DOMINANT_AREA_RATIO = 0.5    # 图占页 ≥50%
IMAGE_DOMINANT_TEXT_MAX = 200      # 同时文本 <200 字符

ImageClass = Literal["skip", "large", "vertical", "normal"]


def classify_image(width: int, height: int) -> ImageClass:
    """按尺寸给图片分类。skip=跳过；large=大图待降采样；vertical=纵向待旋转；normal=正常"""
    if width <= 0 or height <= 0:
        return "skip"
    # logo/装饰：任一边 <100 或面积 <10000
    if width < LOGO_MIN_SIDE or height < LOGO_MIN_SIDE:
        return "skip"
    if width * height < LOGO_MAX_AREA:
        return "skip"
    # 大图
    if width * height >= LARGE_IMAGE_PIXELS:
        # 如同时是纵向的大图，降采样后再看要不要旋转，这里先标 large 优先处理
        return "large"
    # 纵向
    if height > width * VERTICAL_RATIO:
        return "vertical"
    return "normal"


# ------------------------------------------------------------------
# 图像预处理（降采样 + 旋转）
# 输入统一为 (bytes | PIL.Image)，输出统一为 PNG bytes 给 RapidOCR
# ------------------------------------------------------------------


def _downsample_if_large(img):
    """>4M 像素 → 按比例缩到 ≤4M；否则原图返回"""
    w, h = img.size
    pixels = w * h
    if pixels <= LARGE_IMAGE_PIXELS:
        return img
    ratio = (LARGE_IMAGE_PIXELS / pixels) ** 0.5
    new_w = max(1, int(w * ratio))
    new_h = max(1, int(h * ratio))
    from PIL import Image  # 延迟导入
    return img.resize((new_w, new_h), Image.LANCZOS)


def _rotate_if_vertical(img):
    """h > 1.5w → 逆时针 90° 转成横向"""
    w, h = img.size
    if h > w * VERTICAL_RATIO:
        return img.rotate(90, expand=True)
    return img


def prepare_image_for_ocr(image_bytes: bytes) -> bytes:
    """通用图像预处理：先降采样再旋转，输出 PNG bytes"""
    from PIL import Image
    with Image.open(io.BytesIO(image_bytes)) as im:
        im = im.convert("RGB")
        im = _downsample_if_large(im)
        im = _rotate_if_vertical(im)
        out = io.BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()


# ------------------------------------------------------------------
# 页级判断：图片主导页
# ------------------------------------------------------------------


def is_image_dominant_page(page, text_len: int) -> bool:
    """判断该页是否"图占 ≥50% + 文本 <200 字"。
    若是则整页栅格化走 OCR，避免漏字。
    text_len 由调用方传入（已抽好的文本长度）。
    """
    if text_len >= IMAGE_DOMINANT_TEXT_MAX:
        return False
    try:
        page_area = page.rect.width * page.rect.height
        if page_area <= 0:
            return False
        img_area = 0.0
        # get_images(True) 返回 [(xref, smask, w, h, bpc, cs, alt, name, filter, ...)]
        # 但没有位置。位置要从 page.get_image_info() 拿
        for info in page.get_image_info():
            bbox = info.get("bbox")
            if not bbox:
                continue
            x0, y0, x1, y1 = bbox
            img_area += max(0, x1 - x0) * max(0, y1 - y0)
        return (img_area / page_area) >= IMAGE_DOMINANT_AREA_RATIO
    except Exception:
        return False


# ------------------------------------------------------------------
# 页内嵌入图提取
# ------------------------------------------------------------------


def extract_embedded_images(page, doc) -> List[Tuple[str, bytes]]:
    """抽出该页里所有值得 OCR 的嵌入图。
    返回 List[(class, image_bytes)]，class ∈ {large, vertical, normal}
    自动跳过 skip 类。
    doc 是 fitz.Document（extract_image 需要）
    """
    out: List[Tuple[str, bytes]] = []
    try:
        img_list = page.get_images(full=True)
    except Exception:
        return out

    seen_xrefs = set()  # 同页同 xref 只处理一次
    for entry in img_list:
        try:
            xref = entry[0]
        except Exception:
            continue
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)

        try:
            info = doc.extract_image(xref)
        except Exception:
            continue
        if not info:
            continue
        w = info.get("width", 0)
        h = info.get("height", 0)
        cls = classify_image(w, h)
        if cls == "skip":
            continue
        img_bytes = info.get("image")
        if not img_bytes:
            continue
        # large/vertical/normal 统一走 prepare_image_for_ocr（内部按需降采样/旋转）
        try:
            prepared = prepare_image_for_ocr(img_bytes)
        except Exception:
            continue
        out.append((cls, prepared))
    return out


# ------------------------------------------------------------------
# 自测
# ------------------------------------------------------------------
if __name__ == "__main__":
    # classify 单元测试
    cases = [
        # (w, h, expected)
        (50, 50, "skip"),          # logo
        (99, 500, "skip"),          # 一边 <100
        (150, 150, "normal"),
        (200, 500, "vertical"),     # h/w=2.5
        (500, 200, "normal"),       # 横向普通
        (3000, 2000, "large"),      # 6M
        (100, 100, "skip"),         # 面积=10000 边界 - 我们判 <10000 才 skip
    ]
    print("classify_image 自测")
    for w, h, exp in cases:
        got = classify_image(w, h)
        mark = "OK" if got == exp else "FAIL"
        print(f"  [{mark}] ({w},{h}) -> {got} (exp {exp})")

    # 真实 PDF 试跑
    import sys, pymupdf
    if len(sys.argv) > 1:
        path = sys.argv[1]
        doc = pymupdf.open(path)
        print(f"\n真实 PDF {path} 前 5 页：")
        for pno in range(min(5, doc.page_count)):
            page = doc[pno]
            text = (page.get_text("text") or "").strip()
            dom = is_image_dominant_page(page, len(text))
            embeds = extract_embedded_images(page, doc)
            print(f"  page {pno+1}: text={len(text)} 字 dominant={dom} 嵌入图OCR={len(embeds)}")
        doc.close()
