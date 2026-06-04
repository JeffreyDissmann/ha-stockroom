# Brand assets

Since Home Assistant 2026.3.0, a custom integration can ship its own brand
images directly in this `brand/` folder — Home Assistant serves them through its
brands proxy and they take priority over the central
[home-assistant/brands](https://github.com/home-assistant/brands) CDN, so no
separate submission is needed. See the
[announcement](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api).

## Files

```
icon.png         256×256  dark mark, transparent  (light theme)
icon@2x.png      512×512
dark_icon.png    256×256  light mark, transparent (dark theme)
dark_icon@2x.png 512×512
```

`icon.png`/`icon@2x.png` are used on light backgrounds; `dark_icon.png`/
`dark_icon@2x.png` are used on dark backgrounds. The logo appears on the
Integrations dashboard and device pages on Home Assistant 2026.3.0 or newer;
older cores simply show no logo.

Source artwork: `public/icon.svg` from
https://github.com/JeffreyDissmann/stockroom.
