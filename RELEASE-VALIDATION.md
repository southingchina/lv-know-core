# Public source validation

Date: 2026-10-24. Environment: Windows, Python 3.11 / Node 22 where applicable.

264 passed, 9 skipped. Skipped tests cover private Skill text, private author examples and the live-vault updater that are outside this public core; PUBLIC-SCOPE.json lists them. Four private installation/optional-SDK test modules are not distributed. No real vault or Dify service was modified.

The release tree was copied by an allowlist, with private runtime material and source Git history excluded. This record reports local validation; it is not a CI status or a universal compatibility guarantee.
