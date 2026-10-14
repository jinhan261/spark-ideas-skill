# iOS encoder contract fixtures

These four snapshots contain only synthetic records. They were generated on
2026-10-14 by compiling Spark's shipping `AgentSnapshot.swift` together with
`scripts/test-agent-skill-contract.swift`; they were not hand-crafted as the
reference data and contain no user records, local paths, credentials or account IDs.

The portable tests use these checked-in fixtures. For an independent fresh contract
check against the iOS source, set `SPARK_SWIFT_CONTRACT_FIXTURES` to the temporary
directory produced by that Swift generator. No iCloud, Keychain or device data is
used in either test path.
