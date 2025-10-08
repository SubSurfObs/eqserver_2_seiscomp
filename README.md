# eqserver_2_seiscomp

Some tools to help convert an Eqserver waveform archive into a seiscomp (SDS) archived. Relies on a Java tool, EqConvert to convert PC-SUDS to miniseed, and scart to manage stream name mappings


## Overview

An existing UoM seismic server has archived waveform data from 2012-2025, the archive is related to the EqServer sofware created by SRC. 

The file structure of the data archive is miniute long files in day directories, eg.g 

* `/data/repository/archive/DDSW/continuous/2020/11/08 `

A variety of file formats are present, from different recorders as well different pipilines, e.g. telemetered vs uploaded. The archive also contains a variery of ancillary files, and trigered files often appear in the "continuous" archive folders. There have probablyt been some bugs that have contrubted to thsi. 

The goal is to convert this to a Seiscomp / SDS archive.

**Plan**

* Develop a range of tools that tries to efficiently identify the non-overlapping files in each day subdirectory.
* Use these scripts to generate a report for each station prior to teh conversion
* tailor the scripts if necessary to deal with the nuances and edge case.

**Progress**

* at this stage I have succesfully converted Apollo Bay Gecko data, relying on 2 key scripts: process_day_gecko.sh, copy_files.sh
* copy_files wil probablt by chnages to a series of more robust scritps for Geckos and Echos
* all of the "iter: count scripts are about trying to effciently isolate the continuous files in day directories. 
