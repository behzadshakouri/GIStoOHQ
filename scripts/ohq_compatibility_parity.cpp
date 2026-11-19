// Compile through a wrapper defining GEN_HEADER and GEN_CLASS for one model.
// A fixed-head boundary's large artificial volume must never mask errors in a
// small physical block: use a separate tolerance for every non-rigid block.
#include "System.h"
#include "Script.h"
#include <QCoreApplication>
#include <cmath>
#include <iostream>
#include <iomanip>
#include GEN_HEADER
int main(int argc,char**argv){
 QCoreApplication app(argc,argv);if(argc!=3)return 2;
 System s;s.SetDefaultTemplatePath(std::string(argv[2])+"/");s.SetWorkingFolder(aquiutils::GetPath(argv[1]));s.SetSilent(true);
 Script script(argv[1],&s);s.CreateFromScript(script,std::string(argv[2])+"/settings.json");
 if(s.GetErrorHandler()->Count())return 3;
 s.SetAllParents();s.SetSystemSettings();s.CalcAllInitialValues();
 std::vector<double> initial;
 for(unsigned i=0;i<s.BlockCount();++i)initial.push_back(s.block(i)->GetVal("Storage",Expression::timing::past));
 const double end=s.tend();
 if(!s.Solve(false,false) || s.GetSolutionFailed() || std::abs(s.GetTime()-end)>1e-8)return 4;
 GEN_CLASS m;m.initialize();
 if(m.nBlocks()!=int(s.BlockCount()) || !m.runTo(end) || std::abs(m.time()-end)>1e-8)return 5;
 bool pass=true;unsigned tested=0;std::cout<<std::setprecision(17);
 for(unsigned i=0;i<s.BlockCount();++i){
  if(s.block(i)->isrigid("Storage"))continue;
  double x=s.block(i)->GetVal("Storage",Expression::timing::past),y=m.state(i);
  // Existing parity gate was 0.5% of the LARGEST model state. Retain 0.5%,
  // but scale to each block's own initial/final storage, plus 1e-5m3 absolute.
  double tol=1e-5+.005*std::max(std::abs(initial[i]),std::abs(x));
  bool ok=std::isfinite(x)&&std::isfinite(y)&&std::abs(x-y)<=tol;
  pass=pass&&ok;++tested;
  std::cout<<"BLOCK "<<s.block(i)->GetName()<<" interpreter="<<x<<" kernel="<<y<<" tolerance="<<tol<<" "<<(ok?"PASS":"FAIL")<<"\n";
 }
 if(!tested)return 6;
 std::cout<<(pass?"PARITY_PASS":"PARITY_FAIL")<<" tested="<<tested<<"\n";
 return pass?0:7;
}
