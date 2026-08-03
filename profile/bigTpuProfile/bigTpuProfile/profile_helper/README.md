# Profile chip support

`AKS_defs.py` and `AKSV_defs.py` are generated artifacts. Keep them independent
and do not add shared abstractions or runtime state to those files.

`binary.py` contains the active CDM block readers and the `FixedItemWrapper`
used by generated definitions. The historical
`iter*.profile`/`bmlib*.profile` parser has been removed.

The handwritten integration layer is `chip_registry.py`:

- `ChipProfileSpec` maps runtime architecture values to a generated defs module.
- `ChipProfile` validates and wraps the generated module.
- Per-parse mutable state, such as command-ID wrap detection, belongs in
  `ChipProfile`, not in module globals inside generated files.

To add a chip:

1. Generate an independent `<CHIP>_defs.py`.
2. Generate an independent `regdef_<chip>.py`.
3. Add its metadata and capabilities to `CHIP_SPECS`.
4. Add a concrete context class and register it in `target/context.py`.
5. Add alias, contract, context, and end-to-end tests.

If the defs generator is changed, it should emit the same public API validated
by `REQUIRED_PROFILE_API`; chip differences should be data/configuration rather
than conditionals copied into the main parser.
