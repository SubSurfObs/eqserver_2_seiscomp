## Miniseed data from Geckos

## Zipped files

Here is an example where we have a telemetered and non-telemetered file:

```
'/data/repository/archive/ABM1Y/continuous/2023/10/29/2023-10-29 0001 00 ABM1Y.ms.zip'
/data/repository/archive/ABM1Y/continuous/2023/10/29/20231029_0001_ABM1Y.ms.zip
```

```
unzip '/data/repository/archive/ABM1Y/continuous/2023/10/29/2023-10-29 0001 00 ABM1Y.ms.zip' -d test_unzip_formats
Archive:  /data/repository/archive/ABM1Y/continuous/2023/10/29/2023-10-29 0001 00 ABM1Y.ms.zip
  inflating: test_unzip_formats/2023-10-29 0001 00 ABM1Y.ms  
```

```
unzip '/data/repository/archive/ABM1Y/continuous/2023/10/29/20231029_0001_ABM1Y.ms.zip' -d test_unzip_formats
Archive:  /data/repository/archive/ABM1Y/continuous/2023/10/29/20231029_0001_ABM1Y.ms.zip
  inflating: test_unzip_formats/20231029_0001_ABM1Y.ms  
  inflating: test_unzip_formats/ABM1Y.02000236.ss  
  inflating: test_unzip_formats/ABM1Y.02000236.station.xml
```

So, the underscored zip file (USB/Local file) contains multiple files

How to deal with this. 
