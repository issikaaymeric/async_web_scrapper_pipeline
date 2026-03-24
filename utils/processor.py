import pandas as pd 
from typing import List, Dict

def clean_scraped_data(raw_data: List[Dict]) -> List[Dict]:
    """
    Uses Pandas to clean and validate the list  of dictionaries.
    """
    
    if not raw_data:
        return []
    
    # 1. Load into a Dataframe
    
    df = pd.DataFrame(raw_data)
    
    # 2. Data Cleaning
    
    df = df[df['title'] != 'N/A']
    df = df[df['price'] > 0]
    
    # 3. Remove duplicates
    
    df = df.drop_duplicates(subset=['url'])
    
    # 4. Transformation
    #convert all titles to uppercase
    
    df['title'] = df['title'].str.title()
    
    # 5. Convert back to a list of dictionaries for SQLAlchemy
    # 'records' format matches our Database Model exactly
    return df.to_dict(orient="records")
    