@router.get("/", response_model=List[schemas.PartResponse])
async def search_parts(
    name: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    compatible_make: Optional[str] = Query(None),
    compatible_model: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    condition: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),   # ← HARD CAP prevents runaway queries
    db: AsyncSession = Depends(get_db),
):
    query = select(models.PartListing)

    if name:
        query = query.where(models.PartListing.name.ilike(f"%{name}%"))
    if category:
        query = query.where(models.PartListing.category.ilike(f"%{category}%"))
    if compatible_make:
        query = query.where(models.PartListing.compatible_make.ilike(f"%{compatible_make}%"))
    if compatible_model:
        query = query.where(models.PartListing.compatible_model.ilike(f"%{compatible_model}%"))
    if location:
        query = query.where(models.PartListing.location.ilike(f"%{location}%"))
    if condition:
        query = query.where(models.PartListing.condition.ilike(f"%{condition}%"))
    if min_price is not None:
        query = query.where(models.PartListing.price_sll >= min_price)
    if max_price is not None:
        query = query.where(models.PartListing.price_sll <= max_price)

    # Pagination — this is the critical fix
    offset = (page - 1) * limit
    query = query.order_by(models.PartListing.created_at.desc()).limit(limit).offset(offset)

    result = await db.execute(query)
    return result.scalars().all()
