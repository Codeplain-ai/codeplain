# Contributing to Codeplain

Thanks for your interest in contributing to Codeplain!

This guide covers the basic workflow for contributing changes to the repository.

---

## Find an Issue

Start by checking the **Issues** tab in the Codeplain repository.

Before starting work:

* Read the issue description and existing comments.
* Check whether someone is already working on it.
* For larger changes, leave a comment before starting.

---

## Fork and Clone the Repository

Fork the Codeplain repository on GitHub, then clone your fork:

```bash
git clone https://github.com/YOUR-USERNAME/codeplain.git
cd codeplain
```

Add the original Codeplain repository as `upstream`:

```bash
git remote add upstream https://github.com/Codeplain-ai/codeplain.git
```

Before starting new work, update your local repository:

```bash
git fetch upstream
git switch main
git merge upstream/main
```

---

## Create a Branch

Create a separate branch for your change:

```bash
git switch -c your-branch-name
```

For example:

```bash
git switch -c fix-windows-installer
```

Keep each branch and pull request focused on one issue or related set of changes.

---

## Make and Test Your Changes

Make your changes and run the relevant tests before submitting a pull request.

Before committing:

* Review your changes.
* Remove temporary files and debugging output.
* Avoid unrelated changes.
* Add or update tests when needed.
* Update documentation when necessary.

Useful commands:

```bash
git status
git diff
```

---

## Commit and Push

Commit your changes with a clear message:

```bash
git add .
git commit -m "Fix Windows installer Python detection"
```

Push the branch to your fork:

```bash
git push -u origin your-branch-name
```

---

## Open a Pull Request

Open your fork on GitHub and create a pull request into the Codeplain `main` branch.

In the pull request:

1. Describe what you changed.
2. Explain why the change is needed.
3. Link the related issue when applicable.
4. Mention how you tested the change.

Review the **Files changed** tab before submitting.

---

## Review Changes

If a maintainer requests changes, update the same branch and push again:

```bash
git add .
git commit -m "Address review feedback"
git push
```

The existing pull request will update automatically.

---

## Contribution Flow

```text
Issue
  ↓
Fork
  ↓
Clone
  ↓
Create branch
  ↓
Make changes
  ↓
Run tests
  ↓
Commit
  ↓
Push
  ↓
Pull request
```

---

## Thank You

Thank you for contributing to Codeplain!
