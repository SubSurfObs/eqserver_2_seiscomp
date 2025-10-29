# eqserver_2_seiscomp

Some tools to help convert an Eqserver waveform archive into a seiscomp (SDS) archived. Relies on a Java tool, EqConvert to convert PC-SUDS to miniseed, and scart to manage stream name mappings

## TO DO /issues

* Only works for Gecko and EchoPro recorders (the vast majority of our UoM archive).
* Test/implement what to do for existing data (should skip if sufficient data already in SDS archive...)
* Add a trap to ensure cleanup even if the script exits early: `trap 'rm -rf "$temp_day_dir"' EXIT INT TERM`
* reconcile the verbose flag in `process_day_` scripts so they can be passed to `process_archive.sh`
* reconcile the KEEP flag for temp dirs (this was implmented but broken in process_day_echo)
* When I removed MSEED IGNORE_STRINGS_GECKO="trig|\.dmx|\.suds|\mseed." it failed for site ABM7Y (Gecko site) Not sure why, and this is not intended
* STATION name is not explicity defined in the `process_archive.sh`, sometimes errant stations end up in the wrong folder. However -  this is generally easy to clean up later. Having the station name ENV set (dynamically) would allow this problem to be solved. There is a detect_station_name script which could be integrated. 


## Quickstart

```
./process_archive.sh /data/repository/archive/HOLS/continuous/2022/05/10 test_sds/
./process_day_echo.sh --verbose /data/repository/archive/HOLS/continuous/2022/05/10 test_sds/
./process_day_gecko.sh /data/repository/archive/DDSW/continuous/2022/05/10 test_sds/ --verbose

```

## Background

An existing UoM seismic server has archived waveform data from 2012-2025, the archive is associated with the EqServer software created by SRC (now unsupported)

The file structure of the data archive is miniute long files in day directories, eg.g 

* `/data/repository/archive/DDSW/continuous/2020/11/08`

A variety of file formats are present, from different recorders as well different pipelines, e.g. telemetered vs uploaded. The archive also contains a variery of ancillary files, and trigered files often appear in the "continuous" archive folders. There have probablyt been some bugs that have contrubted to thsi. 

The goal is to convert this to a Seiscomp / SDS archive.

## workflow

Process one station’s raw archive (years → months → days) and import it into an SDS archive, choosing the right per-day workflow (Echo vs Gecko), while staying robust, restart-safe, and well-logged.

The moving parts (workflow/)

* process_archive.sh – the top-level driver. You point this at a year, month, or a single day.
* check_recorder_type.sh – decides Echo vs Gecko for a day (fast, single directory scan).
* process_day_echo.sh – handles Echo days (DMX → MiniSEED → SDS).
* process_day_gecko.sh – handles Gecko days (MS/MS.ZIP → sorted MiniSEED → SDS).
* find_files_echo.sh – for Echo, picks the best DMX set (prefers underscore files if “near complete”).
* generate_remap_string.sh – inspects the sorted MiniSEED and makes the SCART mapping strings.

Configuration knobs (config.txt)

Some key settings you can tweak without touching code:

* EQCONVERT_PATH – how to run eqconvert.jar.
* IGNORE_STRINGS – file name patterns to skip.
* THRESHOLD_MISSING_FILES – for Echo: how many missing underscore files are still “near complete” (e.g., 60).
* MIN_FILE_THRESHOLD – day must have at least this many files to trust a mixed-type classification (e.g., 100).
* NET, LOC, CH – target network/location/channel base for remapping (e.g., VX, 00, CH).

How the top-level driver works (process_archive.sh)

1.	You run it on a station subtree (year/month/day all OK):

```
./process_archive.sh [--verbose] /path/to/ABM1Y/ /path/to/SDS/ [optional_temp_base]
```

2.	It figures out what you gave it:

* If it sees data files → DAY.
* If it sees ~28–31 two-digit subdirs → MONTH.
* If it sees ≤12 two-digit subdirs → YEAR.
* It recedes down to day level and for each day:
* sets up station-scoped logging (logs/ABM1Y/…).
* Calls check_recorder_type.sh to pick Echo or Gecko (with the “previous day” fallback for tiny days).
* Skips the day if it appears already present in SDS (simple idempotency guard).
* Runs the right per-day script.


How recorder type is chosen (checkRecorderType.sh)


What a per-day Echo run does (process_day_echo.sh)

1.	Temp workspace: creates a unique dir under $TMPDIR or /tmp by default; if you pass a temp_base, it uses that (and keeps it for debugging).
2.	Pick DMX files: find_files_echo.sh:
* Groups by underscore (local) vs space (telemetered).
* If underscore files are “near complete” (≥ 1440 − threshold), uses only those; otherwise uses all.
3.	Convert in parallel: uses nproc to set threads and runs eqconvert.jar with GNU Parallel.
4.	Sort/merge: scmssort -u -E → one sorted.mseed.
5.	Map channels: generate_remap_string.sh walks streams, builds:
* --rename map like AB.ABM2Y.60.DLZ:VX.ABM2Y.00.CHZ,...
* -c selector like DL?
6.	Import to SDS: scart -I sorted.mseed --with-filecheck "$SDS" -c "$spec" --rename "$map".
7.	Cleanup: temp dir auto-deleted (unless you explicitly provided temp_base).

What a per-day Gecko run does (process_day_gecko.sh)
* Unzips any *.ms.zip (quiet), concatenates minutes in order into full.ms.
* Sort/merge with scmssort → sorted.mseed.
* scart import (no remap, unless you add one).
* Cleans up the temp workspace.

Logging & crash reporting (station-aware)
* Logs are under logs/<STATION>/…
* master.log: one summary line per day (status, date, type).
* YYYY/MM/DD_day.log (or flat DD_day.log if you prefer): full per-day console output via tee.
* crash_reports/: if anything throws, a trap writes a crash file with the day path and last lines of the day log.
* Old logs can be compressed/rotated periodically to prevent bloat.

Restarts & duplicates
* Safe to re-run: days already present in SDS are skipped.
* --with-filecheck in scart helps avoid writing duplicates.
* If you want a hard rebuild of a day, add an overwrite behaviour later (simple flag to bypass the skip).

Verbosity & noise control
* Top-level --verbose turns on chatty logs; otherwise it’s quiet, printing only important info/errors
* VERBOSE_MODE is exported, so helpers (find_files_echo.sh, generate_remap_string.sh, checkRecorderType.sh) follow the same noise level.

Where thresholds live (and why)
* Echo completeness (underscored files): THRESHOLD_MISSING_FILES (e.g., 60) → prefer locals if “near complete”.
* Recorder decision minimum: MIN_FILE_THRESHOLD (e.g., 100) → don’t trust mixed ratios on tiny days.
* Both are in config.txt, override-able per run if you add params.



## Naming conventions

The 1st letter BAND code in SEED convention is: 

| Code | Description               | Frequency (Hz)       | Duration   |
|------|---------------------------|--------------------|-----------|
| D    | …                         | ≥ 250 to < 1000    | < 10 sec  |
| C    | …                         | ≥ 250 to < 1000    | ≥ 10 sec  |
| E    | Extremely Short Period    | ≥ 80               | < 10 sec  |
| S    | Short Period              | 10 to < 80         | < 10 sec  |
| H    | High Broad Band           | ≥ 80               | ≥ 10 sec  |
| B    | Broad Band                | >=	10 < 80         | ≥ 10 sec  |



The 2nd letter: Instrument Code

H High Gain Seismometer
L Low Gain Seismometer
N Accelerometer

Our EQServer archive consists of at least (D,C,E)(H,L,N) but not necessarily correctly applied. For intance, Geckos don't attempt to knwo about the Naturual period, and just apply "C" for a seismometer (our 2 Hz geophones as well as out 60 Sec BBSs). EchoPro data, aftern conversion to Miniseed results in mainly "D", some "E".

The plan is to convert all seismomter data to "C". This simplifies channel names and allows consistency across chnages in instrument - focus on metadata epochs. 

