# 2D layer canvas above 1080p (patch set hwaoi_ui_canvas)

## How the game shows its 2D layer

The game (KT "kids" engine, screen layouts) lays the 2D layer out on a 1920x1080 canvas, in coordinates centred on
the screen (quads of -960..960 x -540..540). Three kinds of draws reach the window image:

1. **Perspective canvas** (frames, panels, menus, title/splash logos, movies' quads, HUD frames, minimap outline,
   map image): a screen camera with fov 45 deg, near 0.1, far 20000, standing at `size.h/2 / tan(22.5 deg)` from the
   canvas. In the UBO (vertex stage, binding 0) the combined matrix row `0 0 -1.00001 -1 | 0 0 d' d` shows `d`:
   1303.68 at 1080p, 1738.23 at 1440p, 869.1 at 720p. With `size` = output the canvas covers 1080/output_h of the
   screen: centred 0.75 at 1440p, cropped 1.5x at 720p (the Map screen at 720p is zoomed and loses its frame).
2. **Text** (glyph quads built on the CPU, vertices in output pixels): anchor * (output / layer size) + output/2.
   With layer size = output they sit 1:1 in a centred 1920x1080 box above 1080p.
3. **Instanced icons** (map icons, minimap markers; instance rows `1.2 0 0 tx | 0 1 0 ty`): positions in canvas
   units from the top-left, identical at 720p and 1080p, drawn with the pixel ortho of the target. Above 1080p they
   shrink towards the upper left; at 720p they spread to the lower right.

The pixel ortho of every 2D draw is computed on the render thread from the bound target's size:
`(2*s/W, -2*t/H, -1, 1)` with s, t at ctx+0x28f4/0x28f8 (1.0 observed), in six copies at main+7194c8, 71cea4,
71de6c, 71ede0, 71f400, 726090. Forcing it to 2/1920 for the window target (host experiment) fixes the icons but
moves the text (output pixels) - the two use different spaces, so it is not a fix by itself.

## The patch

The layer setup at main+576f80 reads the layer's screen size from its config (`ldr x25, [x8]` at 576ff8, x8 =
config+0x88; 1920x1080 when absent), derives the perspective camera (inlined at 5770d0..57712c, tanf at 57711c),
stores the size at layer+0x690 and passes it to main+add610(camera, mode, size) for its cameras. hwaoi_ui_canvas
loads {1920,1080} from patch page +0xa8 there instead, and in add610's perspective path (add844/add874) for its
other callers. Result: the perspective canvas fills the output at every size and text scales with it.
HWAOI_UI_CANVAS=0 (TOTK_UI_CANVAS) restores the original code; the host enlargement in vk_exec_draw.c
(HWAOI_UI_FILL) is off whenever the set is on.

## Not solved: instanced icons

Map icons and minimap markers stay in canvas units at the output's pixel ortho. Leads:
- Their world matrices (column-major, translation = canvas position) live in heap objects found by scanning for the
  instance translation (e.g. 879.175 / 534.005 on the Map screen); an mprotect write-watch on those pages did not
  fire within 40 s (stale copies or per-frame ring slots).
- Candidates to try next: find the code that turns map/minimap positions into those matrices (it ignores the
  layer/output ratio that text applies), or give these draws a canvas ortho (s,t = W/1920, H/1080 at ctx+0x28f4 for
  the draws of that pass only).
- Disproved: the scale-policy sites of hwaoi_display (01432eb4...) are dynamic resolution, not UI. The 1920/1080
  float pairs near 1e9xxxx are DRS UV clamps. add610 mode 1 (ortho camera) forced to 1920x1080 changes nothing
  visible.

## Method

Temporary probe in nvn_api_cmd.c: on draws into the window image (nvn_is_window_tex of SetRenderTargets' color 0)
log the guest frame-pointer chain, the first 256 bytes of each bound UBO and vertex buffer 0; a host-side rewrite of
the UBO's distance/ortho confirmed each hypothesis before patching. A scan of rw mappings for the camera distance
found the camera objects (near 0.1, far 20000, fov 0.785398), whose constants led to the 577xxx setup.
