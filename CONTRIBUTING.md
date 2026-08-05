# Contributing

Thank you for contributing to Research KB Core. Keep each change bounded,
deterministic, privacy-safe, and reviewable.

## Before Opening A Change

1. Use the repository issue forms for a bug, feature request, or support question.
2. Read `AGENTS.md` and `docs/contributor-guide.md` before changing code or contracts.
3. Do not submit real PDFs, parsed paper text, research notes, credentials, private paths,
   or exports from a private research workspace.
4. Discuss schema, state, ID, path, directory layout, compatibility, or write-authority
   changes before implementation.

## Pull Requests

- Create a focused branch from the current `main` branch.
- Add deterministic tests for behavior changes and use only synthetic-from-scratch fixtures.
- Complete the pull request template, including compatibility, privacy, and validation scope.
- Keep the branch current with `main` and resolve every review conversation.
- Required GitHub checks must pass before merge. The repository uses merge commits.

The detailed local commands and engineering rules are in
[`docs/contributor-guide.md`](docs/contributor-guide.md).

## License

The project is licensed under the Apache License 2.0. Unless you explicitly state
otherwise, a contribution intentionally submitted for inclusion in this project is
provided under the same license, as described in Section 5 of the license.

## Security And Support

Do not report vulnerabilities or exposed credentials in a public issue. Follow
[`SECURITY.md`](SECURITY.md) for private reporting. Use [`SUPPORT.md`](SUPPORT.md)
for the supported public-help boundary.
