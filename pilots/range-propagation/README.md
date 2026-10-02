# Range-propagation source pilot

This is an offline, standard-library skill candidate for checking whether declared
output bounds enclose every result of a small arithmetic graph over bounded SI
inputs. It is a source pilot, not part of the marketplace, the published `v0.4.0`
tag, the community index, or a Claude Science host registration. The repository's
science installer currently uses separately bounded families; promotion requires
an explicit reviewed family and inventory update, not an increase to its 1 MB
resource limit.

From the repository root:

```text
python -I -B -m unittest discover -s tests -p test_range_propagation.py -v
python -I -B pilots/range-propagation/szl-range-propagation/scripts/run.py pilots/range-propagation/szl-range-propagation/assets/example.json
```

The invented rectangle claim deliberately fails: the exact SI area range is
`171/100` to `231/100` m², while the declared range is narrower. The CLI prints
`FAIL_DECLARED_BOUNDS` and exits 1 by design. Exit 0 means the supplied bounds
contain the computed intervals; exit 2 means invalid input or I/O. To retain an
exclusive, input-byte-bound report, pass `--output PATH`. See the candidate
[SKILL.md](szl-range-propagation/SKILL.md) and
[contract](szl-range-propagation/references/contract.md) for the declared input
format and mathematical limits.

The 20 proposed [trigger probes](trigger-eval.json) have not been run against an
agent, so routing rates are `NOT_MEASURED`. Local tests, the independent synthetic
forward trial, and the supported module-shape check do not establish actual
Claude Science import, sidecar acceptance, agent effectiveness, measurement
authenticity, probabilistic coverage, or scientific truth. Promotion needs a
reviewed immutable source revision, complete family/inventory and package checks,
separate host publish/readback receipts, and a retained agent-use pilot. Negative
or unavailable results must remain visible through each stage.
