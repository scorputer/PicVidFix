import cv2
import numpy as np
import os
import argparse

def hex_to_bgr(hex_str):
    """Converts a hex color string to a normalized BGR float array."""
    hex_str = hex_str.lstrip('#')
    r = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    b = int(hex_str[4:6], 16)
    return np.array([b, g, r], dtype=np.float32)

def adjust_gamma(image, gamma=1.0):
    """Applies a non-linear brightness curve to protect highlights and shadows."""
    inv_gamma = 1.0 / gamma
    table = np.array([((i / 255.0) ** inv_gamma) * 255 for i in np.arange(0, 256)]).astype("uint8")
    return cv2.LUT(image, table)

def apply_pipeline(args):
    # Dynamic output naming
    name, ext = os.path.splitext(args.image)
    output_path = f"{name}_out{ext}"
    
    img = cv2.imread(args.image, cv2.IMREAD_UNCHANGED)
    if img is None:
        print(f"Error: Could not read image from path '{args.image}'")
        return

    # Safe separation of Alpha channel for structural safety
    has_alpha = len(img.shape) == 3 and img.shape[2] == 4
    if has_alpha:
        bgr = img[:, :, :3].astype(np.float32)
        alpha = img[:, :, 3]
    else:
        bgr = img.astype(np.float32)

    # -------------------------------------------------------------
    # KNOB 1 & 2: GLOBAL WHITE BALANCE / COLOR CAST NEUTRALIZATION
    # -------------------------------------------------------------
    if args.coords:
        x_start, y_start, x_end, y_end = args.coords
        square_patch = bgr[y_start:y_end, x_start:x_end]
        if square_patch.size > 0:
            measured_bgr = np.mean(square_patch, axis=(0, 1))
            measured_bgr = np.clip(measured_bgr, 1.0, 255.0)
            goal_bgr = hex_to_bgr(args.hex)
            scale_factors = goal_bgr / measured_bgr
            print("\n=== REUSABLE CALIBRATION OFFSET ===")
            print(f"To reuse this balance, add this parameter next time: --offset {scale_factors[0]:.6f} {scale_factors[1]:.6f} {scale_factors[2]:.6f}\n")
            
            bgr[:, :, 0] *= scale_factors[0]
            bgr[:, :, 1] *= scale_factors[1]
            bgr[:, :, 2] *= scale_factors[2]

    elif args.offset:
        scale_factors = np.array(args.offset, dtype=np.float32)
        print(f"Applying direct multiplier parameters: B={scale_factors[0]:.4f}, G={scale_factors[1]:.4f}, R={scale_factors[2]:.4f}")
        bgr[:, :, 0] *= scale_factors[0]
        bgr[:, :, 1] *= scale_factors[1]
        bgr[:, :, 2] *= scale_factors[2]

    # Clamp safely back to normal 8-bit space for native channel adjustments
    bgr = np.clip(bgr, 0, 255).astype(np.uint8)

    # -------------------------------------------------------------
    # KNOB 3: SHADE REMAPPING (Map Blues directly to target color)
    # -------------------------------------------------------------
    if args.remap_blue:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        h, s, v = cv2.split(hsv)
        blue_mask = cv2.inRange(h, args.blue_range[0], args.blue_range[1]).astype(np.float32) / 255.0
        blue_mask = cv2.GaussianBlur(blue_mask, (3, 3), 0)
        
        target_ratios = hex_to_bgr(args.remap_blue)
        total = np.sum(target_ratios)
        if total > 0: 
            target_ratios /= total
        
        v_3d = v.astype(np.float32)
        target_shaded = cv2.merge([v_3d * target_ratios[0] * 3.0, v_3d * target_ratios[1] * 3.0, v_3d * target_ratios[2] * 3.0])
        mask_3d = np.expand_dims(blue_mask, axis=2)
        bgr = (bgr.astype(np.float32) * (1.0 - mask_3d)) + (target_shaded * mask_3d)
        bgr = np.clip(bgr, 0, 255).astype(np.uint8)

    # -------------------------------------------------------------
    # KNOBS 4 - 12: CORE PHOTOGRAPHY CONTROLS
    # -------------------------------------------------------------
    # Keep data as float32 for clean math transformations without pixel clipping artifacts
    bgr = bgr.astype(np.float32)

    # 4. Brightness (Offset adjustment)
    if args.brightness != 0.0:
        bgr += args.brightness

    # 5. Contrast (Scale around mid-gray midpoint 127.5)
    if args.contrast != 1.0:
        bgr = 127.5 + args.contrast * (bgr - 127.5)

    # 6, 7, 8: Saturation, Vibrance, and Exposure via HSV space
    if args.saturation != 1.0 or args.vibrance != 0.0 or args.exposure != 0.0:
        bgr_uint8 = np.clip(bgr, 0, 255).astype(np.uint8)
        hsv = cv2.cvtColor(bgr_uint8, cv2.COLOR_BGR2HSV).astype(np.float32)
        h, s, v = cv2.split(hsv)
        
        if args.exposure != 0.0:
            v *= (2.0 ** args.exposure)
            
        if args.saturation != 1.0:
            s *= args.saturation
            
        if args.vibrance != 0.0:
            vibrance_mask = (255.0 - s) / 255.0
            s += args.vibrance * vibrance_mask * 50.0
            
        hsv = cv2.merge([h, s, v])
        hsv_uint8 = np.clip(hsv, 0, 255).astype(np.uint8)
        bgr = cv2.cvtColor(hsv_uint8, cv2.COLOR_HSV2BGR).astype(np.float32)

    # 9. Highlights Protection / 10. Shadows Recovery
    if args.highlights != 0.0 or args.shadows != 0.0:
        bgr_uint8 = np.clip(bgr, 0, 255).astype(np.uint8)
        gray = cv2.cvtColor(bgr_uint8, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        
        if args.highlights != 0.0:
            hi_mask = np.expand_dims(np.clip((gray - 0.5) * 2.0, 0, 1) ** 2, axis=2)
            bgr += args.highlights * hi_mask * 25.0
            
        if args.shadows != 0.0:
            sh_mask = np.expand_dims(np.clip((0.5 - gray) * 2.0, 0, 1) ** 2, axis=2)
            bgr += args.shadows * sh_mask * 25.0

    # 11. Gamma Correction
    bgr = np.clip(bgr, 0, 255).astype(np.uint8)
    if args.gamma != 1.0:
        bgr = adjust_gamma(bgr, args.gamma)

    # 12. Sharpness (Unsharp Masking technique)
    if args.sharpness > 0.0:
        blurred = cv2.GaussianBlur(bgr, (5, 5), 1.0)
        bgr = cv2.addWeighted(bgr, 1.0 + args.sharpness, blurred, -args.sharpness, 0)

    # Stacking back the safe Alpha Transparency mask if required
    if has_alpha:
        final_img = cv2.merge([bgr[:, :, 0], bgr[:, :, 1], bgr[:, :, 2], alpha])
    else:
        final_img = bgr

    cv2.imwrite(output_path, final_img)
    print(f"Success! Master pipeline processing complete. Image saved to: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Master Image Pipeline processing utility.")
    parser.add_argument("image", type=str, help="Path to input image file")
    
    # Global Cast System Knobs
    parser.add_argument("--coords", type=int, nargs=4, metavar=('X_START', 'Y_START', 'X_END', 'Y_END'), help="Averaging square bounds")
    parser.add_argument("--hex", type=str, default="FFFFFF", help="White balance destination target color")
    parser.add_argument("--offset", type=float, nargs=3, metavar=('B', 'G', 'R'), help="Direct pre-calculated calibration multipliers")
    
    # Targeted Shade Remapping Knob
    parser.add_argument("--remap-blue", type=str, metavar="HEX", help="Remap gradients of blue into gradients of this hex destination color")
    parser.add_argument("--blue-range", type=int, nargs=2, default=[95, 135], metavar=('LOW', 'HIGH'), help="Hue bounds for targeted blue masking")
    
    # Core Photography Adjustment Knobs
    parser.add_argument("--brightness", type=float, default=0.0, help="Shift brightness linearly [-255.0 to 255.0]")
    parser.add_argument("--contrast", type=float, default=1.0, help="Scale image contrast [0.0 to 3.0, Default: 1.0]")
    parser.add_argument("--exposure", type=float, default=0.0, help="Adjust exposure stops [-3.0 to 3.0]")
    parser.add_argument("--saturation", type=float, default=1.0, help="Scale global color intensity [0.0 to 3.0, Default: 1.0]")
    parser.add_argument("--vibrance", type=float, default=0.0, help="Selectively boost low colors [-1.0 to 1.0]")
    parser.add_argument("--highlights", type=float, default=0.0, help="Adjust highlights [-5.0 to 5.0]")
    parser.add_argument("--shadows", type=float, default=0.0, help="Adjust shadows depth [-5.0 to 5.0]")
    parser.add_argument("--gamma", type=float, default=1.0, help="Gamma curve distribution value [0.2 to 3.0, Default: 1.0]")
    parser.add_argument("--sharpness", type=float, default=0.0, help="Enhance image edge definition texture [0.0 to 2.0]")
    
    args = parser.parse_args()
    apply_pipeline(args)