# turbo-overlay
Turbo's Gentoo Overlay

```sh
$ emerge app-eselect/eselect-repository
$ eselect repository add turbo-overlay git https://github.com/TurboCheetah/turbo-overlay
$ emerge --sync turbo-overlay
```

## Migrations

- **deprecated/vesktop-bin** was removed from this overlay. Install
  `net-im/vesktop` from the [GURU overlay](https://wiki.gentoo.org/wiki/Project:GURU)
  instead (`eselect repository enable guru && emerge --sync guru`).
