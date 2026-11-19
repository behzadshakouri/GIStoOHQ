// Diagnostic only: report criteria before and after normal initial-state setup.
// Build against one frozen OpenHydroQual source snapshot, never mixed objects.
#include "System.h"
#include "Script.h"
#include <QCoreApplication>
#include <iostream>
int main(int argc,char** argv) {
    QCoreApplication app(argc,argv);
    if(argc!=4) return 2;
    System system;
    const std::string resources=std::string(argv[2])+"/";
    system.SetDefaultTemplatePath(resources);
    system.SetWorkingFolder(std::string(argv[3])+"/");
    Script script(argv[1],&system);
    system.CreateFromScript(script,resources+"settings.json");
    if(system.GetErrorHandler()->Count()) {
        system.GetErrorHandler()->Flush();return 3;
    }
    auto report=[&](const char* stage){
        ErrorHandler errors=system.VerifyAllQuantities();
        std::cout<<"STAGE "<<stage<<" CRITERIA_ERRORS "<<errors.Count()<<std::endl;
        for(unsigned i=0;i<errors.Count();++i)std::cout<<errors[i]->description<<std::endl;
        for(unsigned i=0;i<system.BlockCount();++i){
            auto block=system.block(i);
            for(const auto* key:{"depth","Storage","theta","theta_initial"})
                if(block->Variable(key))std::cout<<"VALUE "<<block->GetName()<<" "<<key
                    <<" "<<block->GetVal(key,Expression::timing::past)<<std::endl;
        }
    };
    report("constructed");
    system.SetAllParents();
    report("parents_bound");
    system.SetSystemSettings();
    report("settings_applied");
    system.CalcAllInitialValues();
    system.UnUpdateAllVariables();
    report("initial_values");
    // No Solve(), optimization, observation loading or parameter changes.
    return 0;
}
