# Brand assets

Icons for the `bilresa_updater` integration, sized per the
[home-assistant/brands](https://github.com/home-assistant/brands) spec.

HACS and Home Assistant read brand assets from the integration itself:

```
custom_components/bilresa_updater/brand/
├── icon.png       256x256
├── icon@2x.png    512x512
├── logo.png       256x256
└── logo@2x.png    512x512
```

The copy under `brands/custom_integrations/bilresa_updater/` is the layout used when
submitting to home-assistant/brands:

## Submitting to home-assistant/brands

Custom-integration icons are served from the brands repo, so HACS/Home
Assistant only show them after this is merged there:

1. Fork https://github.com/home-assistant/brands.
2. Copy this folder's `custom_integrations/bilresa_updater/` into the fork.
3. Open a PR. The CI checks square dimensions, exact sizes, and trimmed
   transparent borders.

Note: brand reviewers generally expect a clean product/logo image (trimmed,
transparent background) rather than a lifestyle photo, so the current photo
may need replacing before the PR is accepted.
