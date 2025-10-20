from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from urllib.parse import quote, unquote
import asyncio
from playwright.async_api import async_playwright
import re
from datetime import datetime
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Tayara Scraper API", version="1.0.0")

# Response Models
class Product(BaseModel):
    title: str
    price: Optional[str] = None
    location: Optional[str] = None
    date_posted: Optional[str] = None
    image_url: Optional[str] = None
    product_url: str
    description: Optional[str] = None
    seller_name: Optional[str] = None
    seller_contact: Optional[str] = None
    is_delivery_available: Optional[bool] = False

class SearchResponse(BaseModel):
    success: bool
    total_products: int
    products: List[Product]
    error: Optional[str] = None

class ProductResponse(BaseModel):
    success: bool
    product: Product
    error: Optional[str] = None

class TayaraScraper:
    """Scraper class for Tayara.tn using Playwright"""
    
    BASE_URL = "https://www.tayara.tn/ads"
    
    @staticmethod
    def build_url(
        query: str,
        category: str,
        subcategory: str = None,
        city: Optional[str] = None,
        condition: Optional[str] = None,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        page: Optional[int] = 1,
    ) -> str:
        """Build Tayara URL from parameters"""
        
        # Build path segments
        path_segments = []
        
        # Add category
        if category:
            path_segments.append(f"c/{quote(category)}")
        
        # Add subcategory
        if subcategory:
            path_segments.append(quote(subcategory))
        
        # Add location
        if city:
            path_segments.append(f"l/{quote(city)}")
        
        # Add condition
        if condition:
            path_segments.append(f"t/{quote(condition)}")

        # Add query
        if query:
            path_segments.append(f"k/{quote(query)}")
        
        # Build base URL
        url = f"{TayaraScraper.BASE_URL}/{'/'.join(path_segments)}/"
        
        # Add query parameters
        params = []
        if min_price is not None:
            params.append(f"minPrice={min_price}")
        if max_price is not None:
            params.append(f"maxPrice={max_price}")
        if page:
            params.append(f"page={page}")
            
        if params:
            url += "?" + "&".join(params)
        
        return url
    
    async def scrape_products_per_page(self, url: str):
        logger.info(f"Scraping URL: {url}")
        
        products = []
        
        async with async_playwright() as p:
            # Launch Firefox browser
            browser = await p.firefox.launch(
                headless=True,
            )
            
            try:
                context = await browser.new_context(
                    # Use a more complete Firefox user agent
                    user_agent='Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0',
                    # Set viewport
                    viewport={'width': 1920, 'height': 1080},
                    # Set additional context options
                    locale='fr-FR',
                    timezone_id='Africa/Tunis',
                    # Ignore HTTPS errors if any
                    ignore_https_errors=True
                )
                
                # Set longer timeout for page operations
                context.set_default_timeout(60000)  # 60 seconds
                
                page_obj = await context.new_page()
                
                # Set additional page timeouts
                page_obj.set_default_navigation_timeout(60000)
                page_obj.set_default_timeout(30000)
                
                # Navigate to the URL with more robust options
                await page_obj.goto(
                    url, 
                    wait_until='domcontentloaded',
                    timeout=60000
                )
                
                # Wait for products to load - using the actual article selector
                await page_obj.wait_for_selector('article', timeout=10000)
                
                # Extract products - targeting the actual article elements
                product_elements = await page_obj.query_selector_all('article')

                for element in product_elements:
                    try:
                        product_data = await self.extract_product_info(element)
                        if product_data:
                            products.append(product_data)
                    except Exception as e:
                        logger.error(f"Error extracting product: {e}")
                        continue
                
                logger.info(f"Extracted {len(products)} products from page")
                return products
                
            except Exception as e:
                logger.error(f"Scraping error: {e}")
                raise ValueError(f"Scraping failed: {str(e)} for page url {url}")
            finally:
                await browser.close()
    
    async def scrape_products(
        self,
        query: str,
        category: str,
        subcategory: str,
        city: Optional[str] = None,
        condition: Optional[str] = None,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        max_pages: int = 3
    ) -> Dict[str, Any]:
        """Scrape products from Tayara using Playwright"""
        
        all_products = []
        current_page = 1
        
        while current_page <= max_pages:
            try:
                # Build URL for current page
                url = self.build_url(
                    query=query,
                    category=category,
                    subcategory=subcategory,
                    city=city,
                    condition=condition,
                    min_price=min_price,
                    max_price=max_price,
                    page=current_page
                )
                
                logger.info(f"Scraping page {current_page}: {url}")
                
                products_per_page = await self.scrape_products_per_page(url)
                
                # If no products found stop scraping
                if not products_per_page:
                    logger.info(f"No products found on page {current_page}, stopping")
                    break
                
                all_products.extend(products_per_page)
                logger.info(f"Total products so far: {len(all_products)}")
                
                if len(products_per_page) < 30:
                    logger.info(f"Got only {len(products_per_page)} products on page {current_page}, might be last page")
                    break
                
                current_page += 1
                
                # small delay
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Error scraping page {current_page}: {e}")
                current_page += 1
                continue
        
        return {
            "success": True,
            "total_products": len(all_products),
            "products": all_products
        }
    
    async def extract_product_info(self, element) -> Optional[Product]:
        """Extract information from a single product element based on actual HTML structure"""
        
        try:
            # Extract title from h2 with class "card-title"
            title_elem = await element.query_selector('h2.card-title')
            title = await title_elem.inner_text() if title_elem else "No title"
            
            # Extract price from data element
            price_elem = await element.query_selector('data')
            price = None
            if price_elem:
                # Get the value attribute and the text content
                price_value = await price_elem.get_attribute('value')
                price_text = await price_elem.inner_text()
                if price_value:
                    price = f"{price_value} DT"
                elif price_text:
                    price = price_text.strip()
            
            # Extract location and date from the location span
            location_elem = await element.query_selector('svg[viewBox="0 0 20 20"] + span')
            location_and_date = await location_elem.inner_text() if location_elem else None
            
            location = None
            date_posted = None
            if location_and_date:
                # Split by comma to separate location and date
                parts = location_and_date.split(',')
                if len(parts) >= 2:
                    location = parts[0].strip()
                    date_posted = parts[1].strip()
                else:
                    location = location_and_date.strip()
            
            # Extract image URL
            img_elem = await element.query_selector('img')
            image_url = await img_elem.get_attribute('src') if img_elem else None
            
            # Extract product URL from the link
            link_elem = await element.query_selector('a')
            relative_url = await link_elem.get_attribute('href') if link_elem else ""
            product_url = f"https://www.tayara.tn{relative_url}" if relative_url and not relative_url.startswith('http') else relative_url
            
            # Clean and validate data
            if title and title.strip():
                return Product(
                    title=title.strip(),
                    price=price,
                    location=location,
                    date_posted=date_posted,
                    image_url=image_url,
                    product_url=product_url,
                )
            else:
                return None
            
        except Exception as e:
            logger.error(f"Error extracting product info: {e}")
            return None
        
    async def get_product_page_info(self, url: str) -> Product:
        """
        Extract detailed product info from Tayara product URL
        
        Args:
            url (str): Full URL of the product page on Tayara.tn
        
        Returns:
            Product: Product object with detailed information
        """
        
        async with async_playwright() as p:
            browser = await p.firefox.launch(headless=True)
            
            try:
                context = await browser.new_context(
                    # Use a more complete Firefox user agent
                    user_agent='Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0',
                    # Set viewport
                    viewport={'width': 1920, 'height': 1080},
                    # Set additional context options
                    locale='fr-FR',
                    timezone_id='Africa/Tunis',
                    # Ignore HTTPS errors if any
                    ignore_https_errors=True
                )
                
                page = await context.new_page()
                
                # Navigate to product page
                await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                
                # Wait for body content
                await page.wait_for_selector('body', timeout=10000)
                
                # Extract title
                title = "No title"                
                title_elem = await page.query_selector('li.p-2.my-1.text-xs.text-gray-600 span')
                if title_elem:
                    title_text = await title_elem.inner_text()
                    if title_text and title_text.strip():
                        title = title_text.strip()

                # Extract seller name
                seller_name = None                
                seller_elem = await page.query_selector('span.text-sm.font-semibold.text-gray-700.capitalize')
                if seller_elem:
                    seller_text = await seller_elem.inner_text()
                    if seller_text and len(seller_text.strip()) > 0:
                        seller_name = seller_text.strip()
                
                # Extract price
                price = None
                price_elem = await page.query_selector('data')
                if price_elem:
                    price_value = await price_elem.get_attribute('value')
                    if price_value:
                        price = f"{price_value} DT"
                    else:
                        price_text = await price_elem.inner_text()
                        price = price_text.strip() if price_text else None

                # Extract description
                    description_elem = await page.query_selector('p.text-sm.text-start.text-gray-700')
                    if description_elem:
                        full_text = await description_elem.text_content()
                        
                        match = re.search(r'^(.*?)Tel:', full_text, re.DOTALL)
                        if match:
                            description = match.group(1).strip()
                            # Remove extra whitespace and normalize line breaks
                            description = re.sub(r'\s+', ' ', description)
                        else:
                            description = full_text.strip()

                # Extract location and date
                location = None
                date_posted = None
                location_elem = await page.query_selector('div.flex.items-center.space-x-2.mb-1 span')
                if location_elem:
                    text = await location_elem.text_content()
                    if text:
                        text_elems = text.split(',')
                        location = text_elems[0].strip()
                        date_posted = text_elems[1].strip()


                # Extract delivery status
                is_delivery = False
                container = await page.query_selector('span.flex.flex-col.py-1')

                if container:
                    status_elem = await container.query_selector('span:nth-child(2)')
                    if status_elem:
                        status_text = await status_elem.text_content()
                        if status_text:
                            status_text = status_text.strip().lower()
                            is_delivery = status_text == 'oui'

                # Extract contact
                seller_contact = None
                try:
                    buttons = await page.query_selector_all('button[aria-label="Afficher numéro"]')
                    if buttons:
                        phone_button = buttons[1]
                        await phone_button.click()
                        # Wait for the phone number
                        seller_contact_elem = await page.wait_for_selector('a[href^="tel:"]', timeout=5000)
                        
                        if seller_contact_elem:
                            seller_contact = await seller_contact_elem.text_content()
                            if seller_contact:
                                seller_contact = seller_contact[4:].strip()
                except Exception as e:
                    logger.warning(f"Could not extract contact info: {e}")
                
                # Extract image URL
                image_url = None
                img_elem = await page.query_selector('img')
                if img_elem:
                    image_url = await img_elem.get_attribute('src')
                
                return Product(
                    title=title,
                    seller_name=seller_name,
                    seller_contact=seller_contact,
                    date_posted=date_posted,
                    is_delivery_available=is_delivery,
                    price=price,
                    location=location,
                    description=description,
                    image_url=image_url,
                    product_url=url
                )
                
            except Exception as e:
                logger.error(f"Error extracting product info: {e}")
                raise ValueError(f"Failed to extract product info: {str(e)}")
            finally:
                await browser.close()

# Initialize scraper
scraper = TayaraScraper()

@app.get("/")
async def root():
    """API root endpoint"""
    return {
        "message": "Tayara Scraper API",
        "version": "1.0.0",
        "endpoints": {
            "/search": "Search products on Tayara.tn",
            "/product": "Get detailed product information from URL",
            "/docs": "API documentation"
        }
    }

@app.get("/search", response_model=SearchResponse)
async def search_products(
    query: str = Query(..., description="Product name/model (e.g., 'Samsung S20')"),
    category: str = Query(..., description="Product category (e.g., 'Informatique et Multimedias')"),
    subcategory: str = Query(..., description="Product subcategory (e.g., 'Téléphones')"),
    city: Optional[str] = Query(None, description="City/Location (e.g., 'Ariana')"),
    status: Optional[str] = Query(None, description="Product condition: 'Neuf', 'Occasion'"),
    min_price: Optional[int] = Query(None, ge=0, description="Minimum price in DT"),
    max_price: Optional[int] = Query(None, ge=0, description="Maximum price in DT"),
    max_pages: int = Query(3, ge=1, le=50, description="Maximum pages to scrape")
):
    """
    Search for products on Tayara.tn with various filters
    
    Example usage:
    - /search?query=iPhone&category=Informatique%20et%20Multimedias&subcategory=Téléphones&city=Tunis&min_price=1000&max_price=5000&max_pages=2
    """
    
    try:
        # Validate price range
        if min_price is not None and max_price is not None and min_price > max_price:
            raise HTTPException(status_code=400, detail="min_price cannot be greater than max_price")
        
        # Perform scraping
        result = await scraper.scrape_products(
            query=query,
            category=category,
            subcategory=subcategory,
            city=city,
            condition=status,
            min_price=min_price,
            max_price=max_price,
            max_pages=max_pages
        )
        
        return SearchResponse(**result)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Search error: {e}")
        return SearchResponse(
            success=False,
            total_products=0,
            products=[],
            error=str(e)
        )

@app.get("/product", response_model=ProductResponse)
async def get_product_info(
    url: str = Query(..., description="Full URL of the product page on Tayara.tn")
):
    """
    Get detailed product information from a specific Tayara.tn product URL
    
    Example usage:
    - /product?url=https://www.tayara.tn/item/12345678/product-name
    """
    
    try:
        # Validate URL
        if not url.startswith('https://www.tayara.tn/'):
            raise HTTPException(status_code=400, detail="URL must be a valid Tayara.tn product URL")
        
        # Extract product information
        product = await scraper.get_product_page_info(url)
        
        return ProductResponse(
            success=True,
            product=product
        )
        
    except HTTPException:
        raise
    except ValueError as e:
        return ProductResponse(
            success=False,
            product=Product(title="Error", product_url=url),
            error=str(e)
        )
    except Exception as e:
        logger.error(f"Product info error: {e}")
        return ProductResponse(
            success=False,
            product=Product(title="Error", product_url=url),
            error=str(e)
        )

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

# Error handlers
@app.exception_handler(404)
async def not_found_handler(request, exc):
    return JSONResponse(
        status_code=404,
        content={"error": "Endpoint not found"}
    )

@app.exception_handler(500)
async def internal_error_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
