#define _Tmodel_ Thermodynamic
#define _DF_ GradQuasiFermi
#define _QC_ eQuantumPotential
#define _EQUATIONSET_ "Poisson Electron Hole Temperature"

#define _Tmodel_ Thermodynamic
#define _DF_ GradQuasiFermi
#define _QC_ eQuantumPotential
#define _EQUATIONSET_ Poisson Electron Hole Temperature

#define _Vdd_     1.1
#define _Vginit_  -1.

File{
   Grid      = "@tdr@"
   Plot      = "@tdrdat@"
   Current   = "@plot@"
   Output    = "@log@"
}

Electrode{
   { Name="source"    Voltage=0.0 }
   { Name="drain"     Voltage=0.0 }
   { Name="gate"      Voltage=_Vginit_ }
   { Name="bulk" Voltage=0.0 }
}

Thermode{ 
  { Name="bulk" Temperature=300 SurfaceResistance=5e-4 } 
  { Name="drain" Temperature=300 SurfaceResistance=1e-3 } 
  { Name="source" Temperature=300 SurfaceResistance=1e-3 } 
}

Physics{

   _Tmodel_
   _QC_
   Fermi
   EffectiveIntrinsicDensity( OldSlotboom )     
   Mobility(
      DopingDep
      eHighFieldsaturation( _DF_ )
      hHighFieldsaturation( GradQuasiFermi )
      Enormal
   )
   Recombination(
      SRH( DopingDep TempDependence )
   )           
}

Plot{
*--Density and Currents, etc
   eDensity hDensity
   TotalCurrent/Vector eCurrent/Vector hCurrent/Vector
   eMobility hMobility
   eVelocity hVelocity
   eQuasiFermi hQuasiFermi

*--Temperature 
   eTemperature Temperature * hTemperature

*--Fields and charges
   ElectricField/Vector Potential SpaceCharge

*--Doping Profiles
   Doping DonorConcentration AcceptorConcentration

*--Generation/Recombination
   SRH Band2Band * Auger
   ImpactIonization eImpactIonization hImpactIonization

*--Driving forces
   eGradQuasiFermi/Vector hGradQuasiFermi/Vector
   eEparallel hEparallel eENormal hENormal

*--Band structure/Composition
   BandGap 
   BandGapNarrowing
   Affinity
   ConductionBand ValenceBand
   eQuantumPotential
}

Math {
   Extrapolate
   Iterations= 20
   Notdamped= 100
   Method= Blocked
   SubMethod= Pardiso
}

Solve {
   *- Build-up of initial solution:
   NewCurrentPrefix="init_"
   Coupled(Iterations=100){ Poisson _QC_ }
   Coupled{ _EQUATIONSET_ _QC_ }
   
   *- Bias drain to target bias
   Quasistationary(
      InitialStep=0.01 MinStep=1e-5 MaxStep=1
      Goal{ Name="drain" Voltage= _Vdd_  }
   ) { Coupled { _EQUATIONSET_ _QC_} }
  
   *-  gate voltage sweep
   NewCurrentPrefix="IdVgs_"
   Quasistationary(
      InitialStep=1e-3 MinStep=1e-5 MaxStep=1
      Goal{ Name="gate" Voltage= @<2.*_Vdd_>@ }
   ) { Coupled { _EQUATIONSET_ _QC_ }
       CurrentPlot(Time=(Range=(0 1) Intervals=20))
     }
   System("rm init_n@node@_des.plt")
}


