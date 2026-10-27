# Migrating to the canonical SPM calculator

Version 1.0.0 is published on
[PyPI](https://pypi.org/project/spm-calculator/1.0.0/). The downstream
integrations ship in their own packages: `policyengine-us` 2.0 and the
`policyengine` wrapper 6.0 are in progress.

The standalone installation examples target `1.1.0`, which extends the
canonical forecast to 2036 without changing any 2022–2035 value. Its content
digest differs from 1.0.x (see [SPM releases](spm-releases.md)). Existing
downstream pins to `spm-calculator==1.0.0` or `1.0.0.post1` keep the 2022–2035
forecast. Adopting 1.1.0 requires a deliberate pin and lockfile change, and the
new forecast content digest.

## Public API replacement

Version 1.0.0 replaces the legacy calculation path with `SPMUnit`, `load_forecast`,
and the canonical PolicyEngine, Frame, and Axiom adapters. The old `calculator`,
`forecast`, `geoadj`, `nowcast`, and `projection` modules are removed. Their former
imports are not supported. Use the [quickstart](quickstart.md) and integration
guides to migrate; the old formulas are not retained as a parallel runtime.

## Existing PolicyEngine installations

Older `policyengine-us` releases import legacy modules and allow an unbounded
`spm-calculator>=0.2.0` dependency. Their metadata therefore permits 1.0.0 even
though their imports are incompatible. Publishing a major version does not make
pip infer an upper bound. This affects fresh installations and dependency
upgrades outside the protected PolicyEngine service images as well.

Keep the exact calculator constraint when reproducing an older bundle. For
example, in an isolated environment:

```sh
python -m pip install "policyengine[models]==5.2.0" "spm-calculator==0.3.1"
python -m pip check
```

The same calculator constraint is required for any older country or wrapper
release that uses those legacy imports. Retaining only a country-version pin
does not protect its unconstrained transitive calculator dependency. The
published 0.3.1 artifact remains available for historical environments; it is
not installed alongside 1.0.0 in the canonical runtime.

Canonical integration requires the coordinated country model and wrapper
release, the certified source-enriched population, and the calculator hash
declared by that bundle. Do not independently substitute 1.0.0 into an older
wrapper manifest. That coordinated integration ships in `policyengine-us` 2.0
and the `policyengine` wrapper 6.0, both in progress; pin the country and
wrapper versions those releases publish rather than an intermediate build.

## Release sequencing

1.0.0 is published, so the exposure this sequencing guards against is live
rather than prospective. Verify that active old service image builders enforce
0.3.1 and deploy those changes. Publish and verify the source-enriched dataset
before releasing a country model whose default population requires its native
SPM role input. Qualify the final country and wrapper artifacts, their default
dataset and their exact calculator pins before promoting service routes.

The 1.0.0 release notes must carry the legacy-installation constraint above and
link the coordinated release versions. This is a declared breaking dependency
change for older unbounded installations, not a guarantee that every historical
PyPI requirement can resolve to the new runtime. Existing published metadata
and scientific artifacts remain immutable.
