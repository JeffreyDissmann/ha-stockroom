# Brand assets

Home Assistant and HACS do not load integration logos from this repository —
they pull them from the central [home-assistant/brands](https://github.com/home-assistant/brands)
repository, keyed by the integration domain. These files are staged here for
version control and to prepare a future brands submission.

To make the icon appear in Home Assistant / HACS, open a pull request against
`home-assistant/brands` adding:

```
custom_integrations/stockroom/icon.png      # 256x256
custom_integrations/stockroom/icon@2x.png   # 512x512
```

Source artwork: the official Stockroom app icon
(`public/icon-512.png` from https://github.com/JeffreyDissmann/stockroom).
