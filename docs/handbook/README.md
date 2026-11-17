# The Lakehouse Builder's Handbook

### From zero to a production-style data & ML platform on Databricks, with Claude Code as your co-engineer

> Companion to the project `energy-lakehouse-de` and its tutorials (Labs 0–12).
> Goal of this handbook: after working through it, you can take **any** dataset and build a similar end-to-end solution, from understanding the data to a monitored, tested, CI/CD-deployed product, and know *why* each step exists.

---

## How this handbook is organised

| Part | File | Chapters |
| --- | --- | --- |
| **I — Foundations** | [`01-foundations.md`](01-foundations.md) | 1. The two architectures (project + CI/CD), explained box by box · 2. Glossary for complete beginners · 3. The lab lifecycle: how every lab starts and ends with CI/CD |
| **II — Working with Claude** | [`02-working-with-claude.md`](02-working-with-claude.md) | 4. Understanding data before you build · 5. Writing prompts for Claude Code · 6. Maximum automation with Claude Code (incl. an automated "human QA tester") · 7. Fixed commands and the error-finding playbook |
| **III — The labs** | [`03-labs.md`](03-labs.md) | 8. One chapter per lab (0–10): goal, how to start, key prompt, checkpoints, typical errors, exam questions |
| **IV — Transfer and exam** | [`04-transfer-and-exam.md`](04-transfer-and-exam.md) | 9. Pipeline design decisions (incl. "why anomaly detection after forecasting?") · 10. Knowledge transfer to other datasets, with a worked example · 11. Final exam, exercises and a capstone project |
| **V — Worked example** | [`05-rossmann-worked-example.md`](05-rossmann-worked-example.md) | 12–14. The same method on Rossmann retail sales: replay feed, leakage trap, step by step through all labs |

The detailed, step-by-step instructions remain in the lab tutorials (`docs/tutorial/labN-*.md`). This handbook is the **map and the method**; the tutorials are the **road**.

---

## How to use it

**If you are a complete beginner**
1. Read Chapter 1 (architectures) and Chapter 2 (glossary) once, without trying to remember everything.
2. Read Chapter 3: the lab lifecycle is the single most important habit.
3. Then work lab by lab: Chapter 8 section → the full tutorial → the exam questions at the end of the section.

**If you already know some data engineering**
Read Chapters 1, 3, 6 and 9, then go straight to Chapter 10 and start your own dataset.

**If you want to apply this to a new problem**
Chapter 4 (understand the data), Chapter 10 (transfer), and the capstone in Chapter 11.

---

## The one-page summary

```
UNDERSTAND  →  PLAN  →  BUILD (thin slice)  →  VERIFY  →  RELEASE  →  OPERATE  →  IMPROVE
   data        stages      Claude Code          checks      CI/CD      monitoring    tests from
 profile     + brief       + tests             + sanity    staging→   + alerts      every incident
                                                 queries    prod
```

Five questions to ask at **every** checkpoint:

1. **Did it run?** Green, with no warnings ignored.
2. **Is the number plausible?** Check it against domain knowledge.
3. **Does it match the source?** Counts, ranges, totals.
4. **What could be silently wrong?** Empty output, wrong date or time zone, wrong branch, wrong ID.
5. **Is it committed, merged and deployed where I think it is?**

Three rules that prevented most real problems in this project:

- **Stop at the first error.** The next command may assume something that didn't happen.
- **The repo is the truth.** Prod is built only from merged code, never by hand.
- **Every incident becomes a test.** Fix the cause, then add a guard so it can't happen again.
