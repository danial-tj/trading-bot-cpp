// Parse calendar dates without interpreting them in the browser's local zone.
export function validDate(value){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(value))return false;
  const [year,month,day]=value.split('-').map(Number);
  if(year<1900||year>9999)return false;
  const date=new Date(Date.UTC(year,month-1,day));
  return date.getUTCFullYear()===year&&date.getUTCMonth()===month-1&&date.getUTCDate()===day;
}
export function dateRangeError(start,end,{required=false,maxDays=null}={}){
  if((required||start)&&!validDate(start))return {field:'start',message:'Enter a real start date as YYYY-MM-DD.'};
  if((required||end)&&!validDate(end))return {field:'end',message:'Enter a real end date as YYYY-MM-DD.'};
  if(start&&end&&end<start)return {field:'end',message:'The end date must be on or after the start date.'};
  if(maxDays&&start&&end&&(Date.parse(end)-Date.parse(start))/86400000+1>maxDays)return {field:'end',message:'Choose a date range of '+maxDays+' days or fewer.'};
  return null;
}
