# Triage report format

`triage.md` has exactly this shape:

    # Log triage

    - ERROR: <n>
    - WARN: <n>
    - INFO: <n>

    **Most frequent: <LEVEL>**

One bullet per level the script reported, in the order ERROR, WARN, INFO, then any
other level alphabetically. Omit a level the script did not report. The last line
names the level with the highest count.
