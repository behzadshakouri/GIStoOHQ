// Deterministic independent overlap oracle; no research inputs.
#include "IntervalRainfall.h"
#include "ohq_timeseries.h"
#include <iostream>
#include <random>
#include <iomanip>
struct Point {double t,c;};
int main(){
 std::mt19937_64 rng(20261119);
 std::uniform_real_distribution<double> u(0.,1.);
 double worstRel=0, worstAbs=0;size_t checks=0;
 for(int fixture=0;fixture<1200;++fixture){
  double cursor=fixture%3==0?36526.:(fixture%3==1?0.:-50.);
  double area=fixture%13==0?0.:1+1e4*u(rng);
  std::vector<double>a,b,d;
  for(int j=0;j<1+fixture%31;++j){
   double start=cursor+(j%3==0?.02*u(rng):0.);
   double end=start+.001+.3*u(rng);
   a.push_back(start);b.push_back(end);d.push_back(j%5==0?0.:.01*u(rng));cursor=end;
  }
  std::vector<Point> native;
  for(auto p:intervalRainfallKnots(a,b,d,area))native.push_back({p.first,p.second});
  ohq::TimeSeries runtime;
  auto scaled=d;for(auto& value:scaled)value*=area;
  if(!runtime.setPrecipitation(a.data(),b.data(),scaled.data(),a.size()))return 2;
  for(int k=0;k<100;++k){
   double start=a.front()-.2+u(rng)*(b.back()-a.front()+.4);
   double dt=.00001+u(rng)*(b.back()-a.front()+.1);
   double end=start+dt;
   long double expected=0;
   for(size_t j=0;j<a.size();++j){
    double overlap=std::max(0.,std::min(end,b[j])-std::max(start,a[j]));
    expected+=static_cast<long double>(d[j])*area*overlap/(b[j]-a[j]);
   }
   for(double actual:{intervalRainfallAverage(native,start,dt)*dt,runtime.average(start,dt)*dt}){
    double error=std::abs(actual-double(expected));
    worstAbs=std::max(worstAbs,error);
    if(expected>1e-10)worstRel=std::max(worstRel,error/double(expected));
    if(!std::isfinite(actual)||error>1e-9+1e-7*std::abs(double(expected))){
     std::cerr<<std::setprecision(17)<<"FAIL fixture="<<fixture<<" start="<<start<<" dt="<<dt<<" actual="<<actual<<" expected="<<double(expected)<<"\n";return 1;
    }
    ++checks;
   }
  }
 }
 std::cout<<std::setprecision(17)<<"PASS checks="<<checks<<" fixtures=1200 max_abs_volume_error="<<worstAbs<<" max_relative_volume_error="<<worstRel<<"\n";
}
