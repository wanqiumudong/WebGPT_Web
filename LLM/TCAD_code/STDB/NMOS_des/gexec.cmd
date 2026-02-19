# project name
name NMOS_des
# execution graph
job 2   -post { extract_vars "$nodedir" n2_dvs.out 2 }  -o n2_dvs "sde -e -l n2_dvs.cmd"
job 3 -d "2"  -post { extract_vars "$nodedir" n3_des.out 3 }  -o n3_des "sdevice pp3_des.cmd"
check sde_dvs.cmd 1756207301
check sdevice_des.cmd 1756207301
check global_tooldb 1749796521
check gtree.dat 1756207302
# included files
