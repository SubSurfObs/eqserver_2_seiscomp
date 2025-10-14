# Centaur digitizers

Data from GA RDK for Woods Point is stored as statiosn: RDK[1,2,3,6]

Data consists of both *mseed.zip and ms.zip, with the former being more frquent, but both seeming to be important (sometimes the *.ms.zip files fill a gap in a sequence of 1 minute .mseed.zip files

files are stored per channel.., at least 9 channels seem to be present. 

Caveats/edge cases

*.ms.zip files don't have channel info 

```
-rw-r--r-- 1 5001 5001  9973 Feb  8  2022 2021-09-26 0004 05 RDK1_HNN.mseed.zip
-rw-r--r-- 1 5001 5001 47499 Feb  9  2022 2021-09-26 0004_RDK1.ms.zip
```

```
unzip "/data/repository/archive/RDK1/continuous/2021/09/26/2021-09-26 0004_RDK1.ms.zip" -d .
...
scart --print-streams -I 2021-09-26\ 0004_RDK1.ms --test
Test mode: Found errors were stated above, if any
# streamID       start                       end                         records samples samplingRate
AU.RDK1.00.HHE   2021-09-26T00:04:01.835Z    2021-09-26T00:05:06.225Z    32 12878 200.0
AU.RDK1.00.HHN   2021-09-26T00:04:10.845Z    2021-09-26T00:05:16.355Z    32 13102 200.0
AU.RDK1.00.HHZ   2021-09-26T00:04:03.8Z      2021-09-26T00:05:09.71Z     32 13182 200.0
AU.RDK1.00.HNE   2021-09-26T00:04:08.225Z    2021-09-26T00:05:13.855Z    32 13126 200.0
# Summary
#   time range: 2021-09-26T00:04:01.835Z - 2021-09-26T00:05:16.355Z
#   networks:   1
#   stations:   1
#   streams:    4
#   records:    128
#   samples:    52288
```

# Guralp Radian 

# Reftek - boreholes

# Peismos
