"""GLSL 330 shader sources."""

FULLSCREEN_VS = """
#version 330
in vec2 in_pos;
in vec2 in_uv;
out vec2 v_uv;
void main() {
    v_uv = in_uv;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

# ---------------------------------------------------------------------------
# Animated background: domain-warped nebula, parallax star field and a
# scrolling synthwave grid. Reacts to level palette, line-clear pulses and
# danger (stack close to the top).
# ---------------------------------------------------------------------------
BACKGROUND_FS = """
#version 330
uniform float u_time;
uniform vec2  u_res;
uniform vec3  u_col_a;
uniform vec3  u_col_b;
uniform vec3  u_col_c;
uniform float u_pulse;
uniform float u_speed;
uniform float u_danger;
in vec2 v_uv;
out vec4 f_color;

float hash21(vec2 p) {
    p = fract(p * vec2(234.34, 435.345));
    p += dot(p, p + 34.23);
    return fract(p.x * p.y);
}

float noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    float a = hash21(i);
    float b = hash21(i + vec2(1.0, 0.0));
    float c = hash21(i + vec2(0.0, 1.0));
    float d = hash21(i + vec2(1.0, 1.0));
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

float fbm(vec2 p) {
    float v = 0.0;
    float a = 0.5;
    mat2 m = mat2(1.6, 1.2, -1.2, 1.6);
    for (int i = 0; i < 5; i++) {
        v += a * noise(p);
        p = m * p;
        a *= 0.5;
    }
    return v;
}

void main() {
    float aspect = u_res.x / u_res.y;
    vec2 p = (v_uv - 0.5) * vec2(aspect, 1.0);
    float t = u_time;

    // --- nebula (domain warping) ---
    vec2 np = p * 2.2;
    vec2 q = vec2(fbm(np + vec2(0.0, t * 0.05)),
                  fbm(np + vec2(5.2, 1.3) - t * 0.04));
    vec2 r = vec2(fbm(np + 3.5 * q + vec2(1.7, 9.2) + t * 0.07),
                  fbm(np + 3.5 * q + vec2(8.3, 2.8) - t * 0.06));
    float f = fbm(np + 3.0 * r);
    vec3 col = mix(u_col_a, u_col_b, clamp(f * f * 2.2, 0.0, 1.0));
    col = mix(col, u_col_c, clamp(length(q) * r.x * 0.8, 0.0, 1.0) * 0.6);
    col *= (f * f * f + 0.6 * f * f + 0.5 * f) * 1.1;
    col += u_col_c * pow(clamp(r.y, 0.0, 1.0), 5.0) * 0.18;
    col += u_col_a * 0.6;

    // --- stars (3 parallax layers) ---
    vec3 stars = vec3(0.0);
    for (int L = 0; L < 3; L++) {
        float fl = float(L);
        float scale = 16.0 + fl * 12.0;
        vec2 sp = p * scale + vec2(fl * 7.3, t * u_speed * (0.35 + fl * 0.3));
        vec2 id = floor(sp);
        vec2 gv = fract(sp) - 0.5;
        float h = hash21(id + fl * 31.7);
        if (h > 0.84) {
            vec2 off = (vec2(hash21(id + 1.3), hash21(id + 7.1)) - 0.5) * 0.7;
            vec2 d2 = gv - off;
            float d = length(d2);
            float tw = 0.55 + 0.45 * sin(t * (1.5 + h * 5.0) + h * 40.0);
            float sz = (0.03 + 0.06 * fract(h * 13.0)) * (1.0 - fl * 0.2);
            float s = smoothstep(sz, 0.0, d) * tw;
            if (h > 0.975) {
                float flare = max(0.0, 1.0 - abs(d2.x) * 30.0) * max(0.0, 1.0 - abs(d2.y) * 3.0)
                            + max(0.0, 1.0 - abs(d2.y) * 30.0) * max(0.0, 1.0 - abs(d2.x) * 3.0);
                s += flare * 0.5 * tw;
            }
            vec3 sc = mix(vec3(0.75, 0.85, 1.0), u_col_c + 0.35, fract(h * 7.0));
            stars += sc * s * (1.0 - fl * 0.25);
        }
    }
    col += stars;

    // --- synthwave floor grid ---
    float horizon = -0.2;
    float depth = max(horizon - p.y, 0.002);
    float z = 0.33 / depth;
    vec2 g = vec2(p.x * z * 1.1, z + t * (0.5 + u_speed * 0.35));
    vec2 dg = abs(fract(g - 0.5) - 0.5);
    vec2 fw = fwidth(g) * 1.3;
    float lx = 1.0 - smoothstep(0.0, fw.x + 0.012, dg.x);
    float ly = 1.0 - smoothstep(0.0, fw.y + 0.012, dg.y);
    float grid = max(lx, ly);
    float below = step(p.y, horizon);
    float fade = smoothstep(0.0, 0.3, depth) * below;
    col = mix(col, col * 0.35 + u_col_a * 0.4, below * smoothstep(0.0, 0.08, depth));
    col += u_col_c * grid * fade * (0.45 + 1.8 * u_pulse);
    col += u_col_b * exp(-abs(p.y - horizon) * 16.0) * (0.45 + 1.2 * u_pulse);
    col += u_col_c * exp(-abs(p.y - horizon) * 90.0) * (0.35 + u_pulse);

    // --- beat pulse and danger tint ---
    col *= 1.0 + u_pulse * 0.4;
    float vig = length(p * vec2(0.75, 1.0));
    float beat = 0.6 + 0.4 * sin(t * 6.5);
    col = mix(col, col * vec3(1.5, 0.35, 0.4) + vec3(0.10, 0.0, 0.01),
              clamp(u_danger * smoothstep(0.15, 0.9, vig) * beat, 0.0, 1.0));

    f_color = vec4(col, 1.0);
}
"""

# Straight alpha game layer (pygame surface) - drawn 1:1 into the scene.
LAYER_FS = """
#version 330
uniform sampler2D u_tex;
uniform float u_flip;
in vec2 v_uv;
out vec4 f_color;
void main() {
    f_color = texture(u_tex, vec2(v_uv.x, mix(v_uv.y, 1.0 - v_uv.y, u_flip)));
}
"""

# Bright pass + 2x downsample with a soft knee threshold.
BRIGHT_FS = """
#version 330
uniform sampler2D u_tex;
uniform vec2 u_texel;
uniform float u_threshold;
uniform float u_knee;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec3 c = texture(u_tex, v_uv + u_texel * vec2(-0.5, -0.5)).rgb
           + texture(u_tex, v_uv + u_texel * vec2( 0.5, -0.5)).rgb
           + texture(u_tex, v_uv + u_texel * vec2(-0.5,  0.5)).rgb
           + texture(u_tex, v_uv + u_texel * vec2( 0.5,  0.5)).rgb;
    c *= 0.25;
    float br = max(c.r, max(c.g, c.b));
    float soft = clamp(br - u_threshold + u_knee, 0.0, 2.0 * u_knee);
    soft = soft * soft / (4.0 * u_knee + 1e-4);
    float contrib = max(soft, br - u_threshold) / max(br, 1e-4);
    f_color = vec4(c * contrib, 1.0);
}
"""

DOWN_FS = """
#version 330
uniform sampler2D u_tex;
uniform vec2 u_texel;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec3 c = texture(u_tex, v_uv + u_texel * vec2(-1.0, -1.0)).rgb
           + texture(u_tex, v_uv + u_texel * vec2( 1.0, -1.0)).rgb
           + texture(u_tex, v_uv + u_texel * vec2(-1.0,  1.0)).rgb
           + texture(u_tex, v_uv + u_texel * vec2( 1.0,  1.0)).rgb;
    f_color = vec4(c * 0.25, 1.0);
}
"""

# 9-tap gaussian using bilinear taps (5 fetches), separable.
BLUR_FS = """
#version 330
uniform sampler2D u_tex;
uniform vec2 u_dir;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec3 c = texture(u_tex, v_uv).rgb * 0.2270270270;
    c += texture(u_tex, v_uv + u_dir * 1.3846153846).rgb * 0.3162162162;
    c += texture(u_tex, v_uv - u_dir * 1.3846153846).rgb * 0.3162162162;
    c += texture(u_tex, v_uv + u_dir * 3.2307692308).rgb * 0.0702702703;
    c += texture(u_tex, v_uv - u_dir * 3.2307692308).rgb * 0.0702702703;
    f_color = vec4(c, 1.0);
}
"""

# ---------------------------------------------------------------------------
# Instanced particles. Positions are in logical pixels (y down).
# kind: 0 glow, 1 spark (velocity streak), 2 shard (solid, alpha blended),
#       3 beam (horizontal band), 4 trail (vertical light), 5 ring
# ---------------------------------------------------------------------------
PARTICLE_VS = """
#version 330
uniform vec2 u_res;
in vec2 in_corner;
in vec2 i_pos;
in vec2 i_size;
in vec2 i_vel;
in vec4 i_color;
in float i_rot;
in float i_kind;
in float i_age;
out vec2 v_local;
out vec4 v_color;
flat out int v_kind;
void main() {
    int kind = int(i_kind + 0.5);
    vec2 size = i_size;
    float angle = i_rot;
    vec4 col = i_color;
    float a = clamp(i_age, 0.0, 1.0);

    if (kind == 0) {
        size *= 1.0 - 0.5 * a;
        col.a *= (1.0 - a) * (1.0 - a);
    } else if (kind == 1) {
        float sp = length(i_vel);
        angle = atan(i_vel.y, i_vel.x);
        size = vec2(size.x * 3.0 + sp * 0.045, size.x * 2.0);
        col.a *= 1.0 - a;
    } else if (kind == 2) {
        size *= 1.0 - 0.3 * a;
        col.a *= 1.0 - smoothstep(0.65, 1.0, a);
    } else if (kind == 3) {
        angle = 0.0;
        float env = a < 0.12 ? a / 0.12 : 1.0 - (a - 0.12) / 0.88;
        size = vec2(size.x * (0.6 + 0.4 * sqrt(a)), size.y * sqrt(max(env, 0.0)));
        col.a *= 1.0 - a;
    } else if (kind == 4) {
        angle = 0.0;
        size.x *= 1.0 - 0.7 * a;
        col.a *= (1.0 - a) * (1.0 - a);
    } else {
        angle = 0.0;
        size *= 0.25 + 0.9 * sqrt(a);
        col.a *= (1.0 - a);
    }

    float c = cos(angle);
    float s = sin(angle);
    vec2 off = in_corner * size * 0.5;
    off = vec2(off.x * c - off.y * s, off.x * s + off.y * c);
    vec2 p = i_pos + off;
    vec2 ndc = p / u_res * 2.0 - 1.0;
    ndc.y = -ndc.y;
    gl_Position = vec4(ndc, 0.0, 1.0);
    v_local = in_corner;
    v_color = col;
    v_kind = kind;
}
"""

PARTICLE_FS = """
#version 330
uniform int u_pass;
in vec2 v_local;
in vec4 v_color;
flat in int v_kind;
out vec4 f_color;
void main() {
    bool shard = v_kind == 2;
    if ((u_pass == 0) != shard) discard;
    vec2 l = v_local;
    vec3 col = v_color.rgb;
    float alpha = 1.0;

    if (v_kind == 0) {
        float d = dot(l, l);
        alpha = exp(-d * 4.5) * (1.0 - smoothstep(0.8, 1.0, d));
        col *= 1.0 + exp(-d * 40.0) * 1.5;
    } else if (v_kind == 1) {
        float across = abs(l.y);
        float along = abs(l.x);
        alpha = (1.0 - smoothstep(0.0, 1.0, across)) * (1.0 - smoothstep(0.2, 1.0, along));
        col *= 1.0 + (1.0 - smoothstep(0.0, 0.4, across)) * 1.5;
    } else if (v_kind == 2) {
        vec2 a = abs(l);
        float edge = max(a.x, a.y);
        float light = 0.75 + 0.45 * (0.5 - 0.5 * (l.y + l.x) * 0.5);
        col *= light;
        col = mix(col, col * 0.45, smoothstep(0.72, 0.8, edge));
        col = mix(col, vec3(1.0), smoothstep(0.55, 0.0, length(l + vec2(0.45, 0.45))) * 0.5);
        f_color = vec4(col, v_color.a * (1.0 - smoothstep(0.92, 1.0, edge)));
        return;
    } else if (v_kind == 3) {
        alpha = exp(-l.y * l.y * 5.0) * (1.0 - smoothstep(0.6, 1.0, abs(l.x)));
        col = mix(col, vec3(2.2), exp(-l.y * l.y * 60.0) * 0.6);
    } else if (v_kind == 4) {
        float grad = smoothstep(-1.0, 1.0, l.y);
        alpha = grad * grad * exp(-l.x * l.x * 1.4) * (1.0 - smoothstep(0.85, 1.0, abs(l.x)));
        col = mix(col, vec3(2.5), exp(-l.x * l.x * 10.0) * grad * 0.45);
    } else {
        float d = length(l);
        float x = (d - 0.85) * 9.0;
        alpha = exp(-x * x);
    }
    f_color = vec4(col * alpha * v_color.a, 1.0);
}
"""

# ---------------------------------------------------------------------------
# Final composite: CRT curvature, screen shake, shockwave distortion,
# glitch, chromatic aberration, bloom, flashes, danger vignette,
# transition pixelation, scanlines and film grain.
# ---------------------------------------------------------------------------
FINAL_FS = """
#version 330
uniform sampler2D u_scene;
uniform sampler2D u_bloom0;
uniform sampler2D u_bloom1;
uniform sampler2D u_bloom2;
uniform vec4  u_view;
uniform vec2  u_res;
uniform float u_time;
uniform vec2  u_shake;
uniform float u_shake_rot;
uniform vec4  u_waves[4];
uniform float u_chroma;
uniform float u_glitch;
uniform float u_flash;
uniform vec3  u_flash_col;
uniform float u_danger;
uniform float u_crt;
uniform float u_bloom;
uniform float u_pixelate;
uniform float u_desat;
in vec2 v_uv;
out vec4 f_color;

float hash(vec2 p) {
    p = fract(p * vec2(443.897, 441.423));
    p += dot(p, p.yx + 19.19);
    return fract((p.x + p.y) * p.x);
}

vec2 crt_curve(vec2 uv, float amt) {
    uv = uv * 2.0 - 1.0;
    vec2 off = abs(uv.yx) / vec2(5.0, 4.0);
    uv = uv + uv * off * off * amt;
    return uv * 0.5 + 0.5;
}

vec3 shoulder(vec3 c) {
    vec3 over = max(c - 0.8, 0.0);
    return min(c, 0.8) + 0.2 * (1.0 - exp(-over / 0.2));
}

void main() {
    vec2 uv0 = (gl_FragCoord.xy - u_view.xy) / u_view.zw;
    if (uv0.x < 0.0 || uv0.x > 1.0 || uv0.y < 0.0 || uv0.y > 1.0) {
        f_color = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }
    float aspect = u_res.x / u_res.y;
    vec2 cuv = crt_curve(uv0, u_crt);

    // shake: rotate around the centre, zoom a touch so edges stay hidden
    vec2 c = cuv - 0.5;
    c.x *= aspect;
    float sr = sin(u_shake_rot);
    float cr = cos(u_shake_rot);
    c = vec2(c.x * cr - c.y * sr, c.x * sr + c.y * cr);
    c /= 1.0 + length(u_shake) * 2.0 + abs(u_shake_rot);
    c.x /= aspect;
    vec2 uv = c + 0.5 - u_shake;

    // transition pixelation
    if (u_pixelate > 0.001) {
        float px = 1.0 + u_pixelate * u_pixelate * 48.0;
        vec2 grid = u_res / px;
        uv = (floor(uv * grid) + 0.5) / grid;
    }

    // shockwaves
    float ring = 0.0;
    for (int i = 0; i < 4; i++) {
        vec4 w = u_waves[i];
        if (w.w <= 0.0) continue;
        vec2 d = uv - w.xy;
        d.x *= aspect;
        float dist = length(d);
        float x = dist - w.z;
        float band = exp(-x * x * 700.0);
        vec2 dir = d / max(dist, 1e-4);
        vec2 offs = dir * band * w.w * 0.03;
        offs.x /= aspect;
        uv -= offs;
        ring += band * w.w;
    }

    // glitch: horizontal slice displacement
    float g = u_glitch;
    if (g > 0.001) {
        float row = floor(uv.y * 42.0);
        float tt = floor(u_time * 24.0);
        if (hash(vec2(row, tt)) < g * 0.35) {
            uv.x += (hash(vec2(row * 1.7, tt + 3.1)) - 0.5) * 0.12 * g;
        }
        float blk = hash(floor(uv * vec2(8.0, 16.0)) + tt);
        if (blk < g * 0.06) uv += (vec2(hash(vec2(tt, blk)), hash(vec2(blk, tt))) - 0.5) * 0.04;
    }

    // chromatic aberration (radial)
    vec2 cd = uv - 0.5;
    float ca = 0.004 + u_chroma * 0.02 + g * 0.02;
    vec3 col;
    col.r = texture(u_scene, uv + cd * ca).r;
    col.g = texture(u_scene, uv).g;
    col.b = texture(u_scene, uv - cd * ca).b;

    // bloom (three blur levels)
    vec3 bl = texture(u_bloom0, uv).rgb * 0.45
            + texture(u_bloom1, uv).rgb * 0.7
            + texture(u_bloom2, uv).rgb * 0.9;
    col += bl * u_bloom * 1.0;

    col += vec3(0.55, 0.8, 1.0) * ring * 0.18;
    col += u_flash_col * u_flash;

    // pause desaturation
    float lum = dot(col, vec3(0.299, 0.587, 0.114));
    col = mix(col, vec3(lum) * vec3(0.8, 0.86, 1.0), u_desat * 0.85);
    col *= 1.0 - 0.3 * u_desat;

    // danger vignette pulse
    float vd = length((uv0 - 0.5) * vec2(aspect, 1.0));
    float beat = 0.5 + 0.5 * sin(u_time * 7.0);
    col += vec3(0.9, 0.04, 0.1) * u_danger * smoothstep(0.45, 1.1, vd) * (0.2 + 0.35 * beat);

    // a little extra vibrance for the neon look
    float lum0 = dot(col, vec3(0.299, 0.587, 0.114));
    col = max(mix(vec3(lum0), col, 1.0 + 0.22 * (1.0 - u_desat)), 0.0);

    col = shoulder(col);

    // CRT: scanlines, aperture mask, vignette, rounded screen edge
    float scan = 0.5 + 0.5 * sin(cuv.y * u_res.y * 3.14159265);
    col *= mix(1.0, 0.8 + 0.2 * scan, u_crt);
    float m = mod(gl_FragCoord.x, 3.0);
    vec3 mask = vec3(m < 1.0 ? 1.06 : 0.96, (m >= 1.0 && m < 2.0) ? 1.06 : 0.96, m >= 2.0 ? 1.06 : 0.96);
    col *= mix(vec3(1.0), mask, u_crt * 0.7);
    vec2 vv = cuv * (1.0 - cuv.yx);
    float vig = pow(clamp(vv.x * vv.y * 16.0, 0.0, 1.0), 0.22 + 0.1 * u_crt);
    col *= mix(1.0, vig, 0.85);
    if (u_crt > 0.001) {
        vec2 e = smoothstep(vec2(0.0), vec2(0.004), cuv) * smoothstep(vec2(0.0), vec2(0.004), 1.0 - cuv);
        col *= mix(1.0, e.x * e.y, u_crt);
    }

    col += (hash(gl_FragCoord.xy + fract(u_time * 7.13) * 100.0) - 0.5) * 0.035;
    f_color = vec4(max(col, 0.0), 1.0);
}
"""
