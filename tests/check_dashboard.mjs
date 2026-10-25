import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE??'playwright');
const browser=await chromium.launch({headless:true,channel:'chromium'});
try {
 const page=await browser.newPage();
 await page.route('https://**/*',async route=>{
  const url=new URL(route.request().url());
  if(url.pathname==='/api/stats')return route.fulfill({json:{total:1,complete:true,backfill_complete:true,snapshot_at:new Date().toISOString(),brands:[],types:[],regions:[],daily:[],sources:[{source:'facebook_marketplace',status:'success',saved:1,observed:1,finished_at:new Date().toISOString()}]}});
  if(url.pathname==='/api/listings')return route.fulfill({json:{items:[{listing_id:'facebook_marketplace:1',source:'facebook_marketplace',subject:'<img src=x onerror="window.pwned=true">',price:null,brand:'Honda',url:'javascript:alert(1)',posted_at:null,observed_at:new Date().toISOString()}],has_more:true,next_cursor:'test-cursor'}});
  return route.fulfill({status:200,contentType:'application/javascript',body:'window.Chart=class {constructor(){} destroy(){}};'});
 });
 await page.goto(pathToFileURL(path.resolve('dashboard/index.html')).href);
 await page.locator('#filter-source').waitFor({timeout:3000});
 await page.locator('.listing-card').waitFor({timeout:3000});
 assert.equal(await page.evaluate(()=>window.pwned),undefined);
 assert.match(await page.locator('.listing-card').innerText(),/Chưa rõ giá/);
 assert.equal(await page.locator('.listing-card a[href^="javascript:"]').count(),0);
 const request=page.waitForRequest(r=>r.url().includes('/api/listings')&&r.url().includes('source=facebook_marketplace'));
 await page.locator('#filter-source').selectOption('facebook_marketplace');
 await request;
 await page.waitForTimeout(100);
 assert.equal(await page.locator('#next-page').isEnabled(),true);
 console.log('PASS: source filter, unknown price, safe rendering and continuation');
}finally{await browser.close();}
