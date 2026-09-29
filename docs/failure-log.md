# Failure Log

Every real failure seen while building and testing, with its cause and fix. The technical write-up (Phase 13) picks the three worst from here, so only failures that actually happened go in.

| # | Date | Phase | What happened | Cause | Fix |
|---|---|---|---|---|---|
| 1 | 2026-09-30 | 1 | `git clone` failed with "git is not recognized", although the project notes listed git 2.44 as installed | Git was not on the machine: no install folder, no registry entry, not on PATH | Ansh installed Git for Windows (2.56.0). New shells need a PATH refresh to see it |
| 2 | 2026-09-30 | 1 | After installing the dependencies, both `opencv-python` and `opencv-python-headless` were present. `pip check` passed, so nothing flagged it | `rapidocr` 3.9.2 depends on `opencv-python`. The design listed `-headless`. Both packages write the same `cv2` folder, so whichever installs last silently overwrites parts of the other | Dropped `-headless`, force-reinstalled `opencv-python`, pinned only that one. Lesson: `pip check` only checks version ranges, not two packages owning the same files |
| 3 | 2026-09-30 | 1 | `npm create vite` failed in PowerShell: "npm.ps1 cannot be loaded because running scripts is disabled" | Windows' default PowerShell execution policy blocks `.ps1` scripts, including npm's wrapper | Called `npm.cmd` instead, which isn't affected. Execution policy left unchanged. The README must mention this for Windows graders |
| 4 | 2026-09-30 | 1 | The plan said the Vite template's ESLint would lint the frontend, but no ESLint was installed | The current `create-vite` React template ships oxlint instead of ESLint | Kept oxlint (`npm run lint` still works) and updated the plan and design wording |
| 5 | 2026-09-30 | 1 | The Gemini spike's first call failed with 404 NOT_FOUND, even though `gemini-2.5-flash` appeared in the key's model list | Google has closed 2.5 Flash to new API users. The model list still shows it, so being listed doesn't mean it can be called | Pinned `gemini-3.8-flash`, which the error message named as the replacement. One structured call passed. Lesson: pin a model only after a real call succeeds |
