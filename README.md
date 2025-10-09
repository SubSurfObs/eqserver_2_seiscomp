# eqserver_2_seiscomp

Some tools to help convert an Eqserver waveform archive into a seiscomp (SDS) archived. Relies on a Java tool, EqConvert to convert PC-SUDS to miniseed, and scart to manage stream name mappings


## Overview

An existing UoM seismic server has archived waveform data from 2012-2025, the archive is related to the EqServer sofware created by SRC. 

The file structure of the data archive is miniute long files in day directories, eg.g 

* `/data/repository/archive/DDSW/continuous/2020/11/08 `

A variety of file formats are present, from different recorders as well different pipilines, e.g. telemetered vs uploaded. The archive also contains a variery of ancillary files, and trigered files often appear in the "continuous" archive folders. There have probablyt been some bugs that have contrubted to thsi. 

The goal is to convert this to a Seiscomp / SDS archive.

**Plan/progress**

Summary – EQ Server to Syscomp Conversion Workflow

I’ve made some progress on the data conversion workflow, particularly using Seiscomp's scmssort and scart. 

scmssort handles duplicate files effectively — which is important since the EQ Server archive contains both telemetered and locally stored files that often share identical timestamps. scmssort identifies these as duplicates, allowing us to copy entire batches of files and let it clean up automatically, avoiding the need for complex manual filtering.

To simplify further, I suggest copying all channel types (including accelerometer and seismometer files) and then running a test SCART output (Syscomp Archive Tool). 

Since scart reports the number of channels, we can filter based on that (e.g., retaining only six-channel sets).

Next, I’ll set this up using configuration files — one for Geckos and one for Echoes. 

The workflow logic would be:

	1.	Identify whether the station data is Gecko or Echo (straightforward).
	2.	Load the corresponding config file, which defines files/patterns to ignore and expected file formats and remapping paramters.
	3.	Prioritize common cases by checking which file formats are most prevalent (underscore vs. space-separated).
	4.	If enough underscored files exist (most common case), copy only these and process directly; otherwise, include both underscore and spaced versions and let scmssort reconcile duplicates automatically. WHY NOT JUST SIMPLIFY and copy all files all of the time. 
  5.  use scart to determin the original channels and names (3 or 6); pares this output to determine channel remapping string, and a channle exclusion string.  

I’ll first implement a minimal working version before expanding with configuration logic.

Then I’ll add reporting metrics to capture edge cases, so we can identify when custom configs are needed.

Once stable, the goal is to parallelize the workflow — ideally processing one station per day.


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

