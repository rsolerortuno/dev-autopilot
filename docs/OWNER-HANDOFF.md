# Owner actions and autonomous work

Development is on `implementation/v1`, with draft PR
https://github.com/rsolerortuno/dev-autopilot/pull/13.
The target is 1.0.0; the package remains a development version until acceptance.

## Work the coordinator can execute

- Implement and independently review changes in isolated Luna worktrees.
- Commit, push, maintain the draft PR and resolve GitHub CI failures.
- Run offline evaluations, local recovery tests and Linux/Docker checks in CI.
- Run an eight-hour offline durability soak and preserve its incremental report.
  Completion requires the measured duration; starting the process is not a pass.
- Build packages, synchronize the pinned notebook and verify Drive transfers.
- Prepare the portfolio demo, evidence, runbooks and final review checklist.

CI and CodeQL passed for `e1ecfc101d4b940a18a17b0dd2c87c8b557a06c1`,
including Python 3.11, 3.12 and 3.13. Later commits need their own CI results.

## Actions requiring the owner's account or decision

1. Authorize a maximum real-provider evaluation spend. Model selection is
   delegated to the coordinator. Configure any needed credentials in the
   environment or Colab Secrets; never send keys in chat or commit them.
2. Open the [worker notebook](https://colab.research.google.com/drive/1lmi8mNys22nKd8myjiSxrA6Eak7Sdglf),
   allocate a Colab runtime and authorize Drive. Execute the configuration,
   mount, checksum and installation cells in order. The notebook is also under
   `notebooks/dev_autopilot_colab_worker.ipynb` in this repository and in the
   project's Drive `notebooks` folder. Use one Drive worker.
3. Record the portfolio presentation and explain the engineering tradeoffs in
   your own words. The coordinator can prepare the script and evidence.
4. Review the final candidate and explicitly approve release publication.

An offline soak does not establish Colab interruption/recovery or actual provider
quality and billing. These acceptance gates remain distinct.
