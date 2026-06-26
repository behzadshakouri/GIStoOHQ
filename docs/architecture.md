# Architecture

The repository separates model construction into four layers:

1. Readers
2. Internal watershed objects
3. Validators
4. OHQ writers

Only `ohqbuilder/writers/` should need changes when the exact OHQ grammar is refined.
