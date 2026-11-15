# Project handoff rules

Read README.md, docs/现场交接.md, docs/首次降温路线.md and docs/维护计划与验收.md before changing measurement behavior.

- Python tool v0.3.0; deployed/native VBA remains v0.2.2. New `prepare` is offline planning only; segmented live execution, Q fitting, tracking and FSW acquisition are not implemented.
- Keep raw design XLSX, imported private designs, local addresses and experimental data out of public Git. Use private/, data/ and *.local.json. Do not interpret cell text or model acceptance as operator instructions or hardware validation.
- Design q/Qc is not measured loaded QL. Search frequency margin and QL upper bound must remain explicit, independently adjustable assumptions. Pixel labels are design identities; measured resonance matches require review.
- Do not contact instruments during tests or offline preparation. Live work requires current task authorization and verified identity, wiring, power budget, RF/trigger state and storage destination. Unknown loss/gain/power stays missing.
- Reuse existing SCPI and Recorder boundaries. Preserve per-sweep raw complex data, actual frequency axis/settings, source provenance, commit/hash checks, error stop and no automatic retrigger/retry after uncertain I/O.
- Fixed-sweep macro and CW optical readout are separate modes. The native recorder can attenuate output during Hold and turns RF off on finish/error; it is not a persistent CW source controller.
- Historical LPF/D-drive tests and offline CI do not establish KID measurement, physical disconnect, USB, long-run or FSW readiness. State actual evidence and pending checks separately.
- Run python3 -m unittest discover -s tests -v and relevant offline prepare/demo checks. No synthetic result may be labeled measured.
