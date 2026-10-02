# Public OSF registration witness pilot

This opt-in pilot adds a bounded, standard-library-only, no-key readback for a specific
public OSF registration file and its DataCite DOI. It is deliberately outside the released
science-skill marketplace: several active PRs own the shared installer and inventory.

From the repository root:

```text
python -B -m unittest discover -s tests -p test_osf_registration_witness_pilot.py -v
python -B pilots/osf-registration-witness/szl-osf-registration-witness/scripts/run.py witness.json
```

The [skill instructions](szl-osf-registration-witness/SKILL.md) and
[contract](szl-osf-registration-witness/references/contract.md) specify the exact source-pinned
config and outcome boundary. The tests use synthetic provider responses, including blocked and
unavailable cases. A live public example may exercise `opaque_file` transport only; it must
not be represented as an SZL preregistration or a canonical plan witness.

OSF is [open-source Apache-2.0](https://github.com/CenterForOpenScience/osf.io/blob/develop/LICENSE)
and its public API supports unauthenticated reads. DataCite's public REST API can read
findable DOI metadata without a key. These providers supply evidence to inspect, not an
endorsement of this pilot or proof of scientific truth.
