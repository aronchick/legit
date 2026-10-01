# Deployment candidate checklist

The current public endpoint https://legitpr.dev returned HTTP 401 with Basic-auth challenge on 2026-09-09. That verifies the outer access gate only; deployed revision, provider access, usable personas and feedback persistence have not been verified.

Before deploying:

- Complete local gates without warnings; record exact candidate revision.
- Inspect generated profile and retrieval artifacts for each exposed persona; show others as unavailable.
- Save at least two actual reviews with concrete useful findings and one honest miss. Do not advertise an aggregate evaluation score as clean evidence.
- Obtain explicit approval for the concrete deployment candidate. No outreach is authorized by deployment approval.

Deployment verification, after approval:

- Record previous deployed revision and runtime-data snapshot for rollback.
- Use documented Hetzner user service and protected access; do not expose an unauthenticated instance.
- Sync the matching profiles and retrieval artifacts together; compare hashes with candidate evidence.
- Verify public-PR scope and an authenticated review through the deployed UI using the configured model.
- Submit feedback and verify the stored review UUID/output and feedback survive service restart.
- Record actual deployed revision, model response metadata, PR URL, review UUID and feedback read-back.
- Stop any temporary local QA servers.

Then fill the invitation's access/example placeholders and request authorization to send it. First external completion and feedback are separate tasks, not a consequence of a green build.
