#include "System.h"
#include "Script.h"
#include <QCoreApplication>
#include <fstream>
#include <iomanip>
int main(int argc,char**argv){
 QCoreApplication app(argc,argv); if(argc!=4)return 2;
 System s; std::string r="/mnt/3rd900/Projects/OpenHydroQual/resources/";
 s.SetDefaultTemplatePath(r);s.SetWorkingFolder(std::string(argv[2])+"/");
 Script script(argv[1],&s);s.CreateFromScript(script,r+"settings.json");
 if(s.GetErrorHandler()->Count()){s.GetErrorHandler()->Flush();return 3;}
 s.SetAllParents();s.SetSystemSettings();
 std::ofstream inv(std::string(argv[2])+"/inventory.tsv");
 for(unsigned i=0;i<s.BlockCount();i++){
  auto b=s.block(i);auto q=b->Variable("Storage");if(!q)continue;
  inv<<"storage\t"<<b->GetName()<<"\t"<<b->GetType()<<"\t"<<b->isrigid("Storage")<<"\n";
  q->SetIncludeInOutput(true);
  for(auto term:q->GetCorrespondingInflowVar())if(!term.empty()&&b->Variable(term)){
   inv<<"external\t"<<b->GetName()<<"\t"<<term<<"\n";b->Variable(term)->SetIncludeInOutput(true);
  }
 }
 for(unsigned i=0;i<s.LinksCount();i++){
  auto l=s.link(i);inv<<"link\t"<<l->GetName()<<"\t"<<l->Get_s_Block()->GetName()<<"\t"<<l->Get_e_Block()->GetName()<<"\n";
  auto flow=l->Get_s_Block()->Variable("Storage")->GetCorrespondingFlowVar();
  if(l->Variable(flow))l->Variable(flow)->SetIncludeInOutput(true);
 }
 if(!s.Solve(false,false)){s.GetErrorHandler()->Flush();return 4;}
 std::ofstream out(argv[3]);out<<std::setprecision(17);
 for(auto &ts:s.GetOutputs()){out<<"#"<<ts.name()<<"\n";for(auto &p:ts)out<<p.t<<","<<p.c<<"\n";}
 return 0;
}
