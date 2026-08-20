# Contributing

Keep changes small, deterministic, and evidence-backed.

1. Add or update a behavior test before changing a gate, cap, ranking rule, or
   authority boundary.
2. Run `pytest -q` and `python career_ops_cli.py plan examples/leads.synthetic.json`.
3. Use synthetic fixtures only.
4. Never add automatic submission, messaging, payment, trading, credential, or
   anti-bot bypass behavior.
5. Explain any changed assumption in the pull request.

Changes that weaken fail-closed behavior or raise the daily cap above three are
out of scope for this repository.
